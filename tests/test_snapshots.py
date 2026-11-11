"""Snapshot rules and leakage tests.

The property tests are the important ones: whatever happens after the snapshot time (prices,
volume, resolution) must not change the snapshot.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from calib.config import DAY, HOUR, STALE_SECONDS
from calib.db import DB_PATH, connect
from calib.models import Market
from calib.snapshots import Drop, PriceSeries, Snapshot, liquidity_bucket, snapshot_market

CLOSE = 1_000 * DAY
H = 7 * DAY
S = CLOSE - H


def market(source: str = "kalshi", open_ts: int = 0, close_ts: int = CLOSE) -> Market:
    return Market(source, "M", "E", "t", "c", open_ts, CLOSE, close_ts, CLOSE + DAY, 1)


def series(rows: list[tuple[int, float, str, float | None, float | None]]) -> PriceSeries:
    df = pd.DataFrame(rows, columns=["ts", "price", "price_type", "volume_cum", "spread"])
    return PriceSeries.from_frame(df)


# ---------------------------------------------------------------- rules


def test_not_open_and_closed_early() -> None:
    s = series([(S - HOUR, 0.5, "mid", 10.0, 0.02)])
    d1 = snapshot_market(market(open_ts=S + 1), s, "T-7d", H)
    d2 = snapshot_market(market(close_ts=S), s, "T-7d", H)
    assert isinstance(d1, Drop) and d1.reason == "not_open_yet"
    assert isinstance(d2, Drop) and d2.reason == "closed_before_snapshot"


def test_kalshi_prefers_tight_mid() -> None:
    s = series([(S - 2 * HOUR, 0.40, "last_trade", 5.0, None), (S - HOUR, 0.55, "mid", 5.0, 0.04)])
    snap = snapshot_market(market(), s, "T-7d", H)
    assert isinstance(snap, Snapshot)
    assert (snap.price_type, snap.p, snap.price_ts) == ("mid", 0.55, S - HOUR)


def test_kalshi_wide_spread_falls_back_to_trade() -> None:
    s = series([(S - 2 * HOUR, 0.40, "last_trade", 5.0, None), (S - HOUR, 0.50, "mid", 5.0, 0.60)])
    snap = snapshot_market(market(), s, "T-7d", H)
    assert isinstance(snap, Snapshot) and snap.price_type == "last_trade" and snap.p == 0.40


def test_kalshi_wide_spread_no_trade_dropped() -> None:
    s = series([(S - HOUR, 0.50, "mid", 0.0, 0.60)])
    d = snapshot_market(market(), s, "T-7d", H)
    assert isinstance(d, Drop) and d.reason == "wide_spread_no_trade"


def test_stale_after_48h() -> None:
    s = series([(S - STALE_SECONDS, 0.5, "mid", 1.0, 0.02)])  # exactly 48h old: outside (s-48h, s]
    d = snapshot_market(market(), s, "T-7d", H)
    assert isinstance(d, Drop) and d.reason == "stale"


def test_price_exactly_at_snapshot_is_visible() -> None:
    s = series([(S, 0.5, "mid", 1.0, 0.02)])
    snap = snapshot_market(market(), s, "T-7d", H)
    assert isinstance(snap, Snapshot) and snap.price_ts == S


def test_polymarket_needs_a_trade() -> None:
    quiet = series([(S - HOUR, 0.505, "pm_display", None, None)])
    live = series(
        [(S - 3 * HOUR, 0.60, "last_trade", 50.0, None), (S - HOUR, 0.62, "pm_display", None, None)]
    )
    d = snapshot_market(market("polymarket"), quiet, "T-7d", H)
    snap = snapshot_market(market("polymarket"), live, "T-7d", H)
    assert isinstance(d, Drop) and d.reason == "stale"
    assert isinstance(snap, Snapshot) and snap.price_type == "pm_display" and snap.p == 0.62


def test_capped_volume_is_unknown_unless_bound_is_decisive() -> None:
    rows = [
        (S - 3 * HOUR, 0.6, "last_trade", 500.0, None),
        (S - HOUR, 0.6, "pm_display", None, None),
    ]
    small = snapshot_market(market("polymarket"), series(rows), "T-7d", H, False, S - 4 * HOUR)
    rows[0] = (S - 3 * HOUR, 0.6, "last_trade", 250_000.0, None)
    big = snapshot_market(market("polymarket"), series(rows), "T-7d", H, False, S - 4 * HOUR)
    assert isinstance(small, Snapshot) and small.liquidity == "unknown"
    assert isinstance(big, Snapshot) and big.liquidity == ">=100k"


@pytest.mark.parametrize(
    ("volume", "bucket"),
    [(None, "unknown"), (0.0, "<1k"), (999.0, "<1k"), (1000.0, "1k-10k"), (1e5, ">=100k")],
)
def test_liquidity_buckets(volume: float | None, bucket: str) -> None:
    assert liquidity_bucket(volume) == bucket


# ---------------------------------------------------------------- leakage properties

price_types = st.sampled_from(["mid", "last_trade"])
rows_strategy = st.lists(
    st.tuples(
        st.integers(min_value=S - 5 * DAY, max_value=CLOSE),
        st.floats(min_value=0.01, max_value=0.99),
        price_types,
        st.floats(min_value=0.0, max_value=0.3),
    ),
    min_size=0,
    max_size=40,
)


def _build(raw: list[tuple[int, float, str, float]]) -> list[tuple[int, float, str, float, float]]:
    """Turn raw draws into a plausible series: cumulative volume is non-decreasing in time."""
    raw = sorted(raw, key=lambda r: r[0])
    out, vol = [], 0.0
    for ts, p, kind, spread in raw:
        vol += 10.0
        out.append((ts, p, kind, vol, spread if kind == "mid" else np.nan))
    return out


@settings(max_examples=300, deadline=None)
@given(rows_strategy, rows_strategy, st.sampled_from(["kalshi", "polymarket"]))
def test_future_rows_never_change_the_snapshot(
    past: list[tuple[int, float, str, float]],
    future: list[tuple[int, float, str, float]],
    source: str,
) -> None:
    past_rows = [r for r in _build(past) if r[0] <= S]
    if source == "polymarket":
        past_rows = [
            (t, p, "pm_display" if k == "mid" else k, v, np.nan) for t, p, k, v, _ in past_rows
        ]
    future_rows = [
        (t + (S - t) + 1 + i, p, k, 1e9, sp)  # strictly after S, absurd volume
        for i, (t, p, k, _, sp) in enumerate(_build(future))
    ]
    m = market(source)
    a = snapshot_market(m, series(past_rows), "T-7d", H)
    b = snapshot_market(m, series(past_rows + future_rows), "T-7d", H)
    assert a == b


@settings(max_examples=300, deadline=None)
@given(rows_strategy)
def test_snapshot_rows_only_use_the_past(raw: list[tuple[int, float, str, float]]) -> None:
    rows = _build(raw)
    snap = snapshot_market(market(), series(rows), "T-7d", H)
    if isinstance(snap, Snapshot):
        assert snap.price_ts <= snap.snapshot_ts
        assert snap.price_ts > snap.snapshot_ts - STALE_SECONDS
        expected = max((v for t, _, _, v, _ in rows if t <= S), default=0.0)
        assert snap.volume_before == expected


@settings(max_examples=100, deadline=None)
@given(rows_strategy, st.integers(min_value=S - DAY, max_value=CLOSE + 400 * DAY))
def test_resolution_time_is_irrelevant(raw: list[tuple[int, float, str, float]], res: int) -> None:
    m = market()
    s = series(_build(raw))
    assert snapshot_market(m, s, "T-7d", H) == snapshot_market(
        replace(m, resolved_ts=res), s, "T-7d", H
    )


# ---------------------------------------------------------------- the real table


@pytest.mark.skipif(not DB_PATH.exists(), reason="run `make data snapshots` first")
def test_snapshot_table_has_no_leakage() -> None:
    con = connect(read_only=True)
    n = con.execute("SELECT count(*) FROM snapshots").fetchone()
    if not n or n[0] == 0:
        pytest.skip("snapshots table is empty")
    bad_price = con.execute("SELECT count(*) FROM snapshots WHERE price_ts > snapshot_ts").fetchone()
    bad_anchor = con.execute("""
        SELECT count(*) FROM snapshots s JOIN markets m USING (source, market_id)
        WHERE s.snapshot_ts >= m.close_ts OR s.snapshot_ts < m.open_ts
    """).fetchone()
    # Recompute volume_before from raw rows strictly at or before the snapshot.
    bad_volume = con.execute("""
        WITH v AS (
            SELECT s.source, s.market_id, s.horizon, s.volume_before,
                   coalesce(max(p.volume_cum), 0) AS recomputed
            FROM snapshots s
            JOIN volume_coverage c USING (source, market_id)
            LEFT JOIN prices p ON p.source = s.source AND p.market_id = s.market_id
                 AND p.ts <= s.snapshot_ts AND p.volume_cum IS NOT NULL
            WHERE c.complete
            GROUP BY ALL
        )
        SELECT count(*) FROM v WHERE abs(volume_before - recomputed) > 1e-6
    """).fetchone()
    con.close()
    assert bad_price == (0,)
    assert bad_anchor == (0,)
    assert bad_volume == (0,)
