"""Leak-free snapshots: the implied YES probability at T-30d, T-7d and T-1d before scheduled close.

Rules (see docs/data_sources.md and the README's methodology section):

* Snapshot time ``s = scheduled_close - horizon``. Resolution time is never used.
* A market counts for a horizon only if it was open at ``s`` (opened at or before, closed after).
* Only observations with ``ts <= s`` are visible. That includes volume.
* Stale: no qualifying activity in ``(s - 48h, s]`` -> dropped and counted, never forward-filled.
* Price: Kalshi uses the bid/ask midpoint when the latest book in the window is at most 10c wide,
  otherwise the latest trade. Polymarket uses its displayed price (it has no historical quotes),
  and requires a real trade in the window as proof of activity.

Run: ``python -m calib.snapshots``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from calib import db
from calib.config import HORIZONS, LIQUIDITY_EDGES, LIQUIDITY_LABELS, MAX_SPREAD, STALE_SECONDS
from calib.models import Market


@dataclass(frozen=True)
class Snapshot:
    source: str
    market_id: str
    horizon: str
    snapshot_ts: int
    price_ts: int
    p: float
    price_type: str
    volume_before: float | None
    liquidity: str


@dataclass(frozen=True)
class Drop:
    source: str
    market_id: str
    horizon: str
    reason: str


@dataclass(frozen=True)
class PriceSeries:
    """One market's observations, sorted by ts. Arrays keep the per-market work vectorized."""

    ts: np.ndarray
    price: np.ndarray
    price_type: np.ndarray
    volume_cum: np.ndarray  # NaN where unknown
    spread: np.ndarray  # NaN where not a quote

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> PriceSeries:
        df = df.sort_values("ts", kind="stable")
        return cls(
            ts=df["ts"].to_numpy(dtype=np.int64),
            price=df["price"].to_numpy(dtype=float),
            price_type=df["price_type"].to_numpy(dtype=object),
            volume_cum=df["volume_cum"].to_numpy(dtype=float, na_value=np.nan),
            spread=df["spread"].to_numpy(dtype=float, na_value=np.nan),
        )

    def visible(self, s: int) -> PriceSeries:
        """Everything knowable at time s, and nothing after."""
        n = int(np.searchsorted(self.ts, s, side="right"))
        return PriceSeries(
            self.ts[:n], self.price[:n], self.price_type[:n], self.volume_cum[:n], self.spread[:n]
        )


def liquidity_bucket(volume: float | None) -> str:
    if volume is None or np.isnan(volume):
        return "unknown"
    return LIQUIDITY_LABELS[int(np.searchsorted(LIQUIDITY_EDGES, volume, side="right"))]


def _latest(series: PriceSeries, mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[-1]) if idx.size else None


def volume_before(seen: PriceSeries, s: int, complete: bool, oldest_ts: int | None) -> float | None:
    """Contracts traded at or before s, from rows already filtered to ts <= s.

    With a complete trade record this is exact. With a capped (partial) record it is only known
    when s is inside the covered range, and then it is a lower bound; it's returned only if that
    bound already clears the top liquidity edge, so the bucket is certain.
    """
    known = seen.volume_cum[~np.isnan(seen.volume_cum)]
    vol = float(known[-1]) if known.size else 0.0
    if complete:
        return vol
    if oldest_ts is None or s < oldest_ts:
        return None
    return vol if vol >= LIQUIDITY_EDGES[-1] else None


def snapshot_market(
    market: Market,
    series: PriceSeries,
    horizon: str,
    seconds: int,
    volume_complete: bool = True,
    volume_oldest_ts: int | None = None,
) -> Snapshot | Drop:
    s = market.scheduled_close_ts - seconds

    def drop(reason: str) -> Drop:
        return Drop(market.source, market.market_id, horizon, reason)

    if market.open_ts > s:
        return drop("not_open_yet")
    if market.close_ts <= s:
        return drop("closed_before_snapshot")

    seen = series.visible(s)
    recent = seen.ts > s - STALE_SECONDS
    vol = volume_before(seen, s, volume_complete, volume_oldest_ts)

    def make(i: int, price_type: str) -> Snapshot:
        return Snapshot(
            market.source,
            market.market_id,
            horizon,
            s,
            int(seen.ts[i]),
            float(seen.price[i]),
            price_type,
            vol,
            liquidity_bucket(vol),
        )

    trades = recent & (seen.price_type == "last_trade")
    if market.source == "kalshi":
        i_mid = _latest(seen, recent & (seen.price_type == "mid"))
        if i_mid is not None and seen.spread[i_mid] <= MAX_SPREAD:
            return make(i_mid, "mid")
        i_trade = _latest(seen, trades)
        if i_trade is not None:
            return make(i_trade, "last_trade")
        return drop("wide_spread_no_trade" if i_mid is not None else "stale")

    # Polymarket: the displayed price, gated on proof of activity.
    display = recent & (seen.price_type == "pm_display")
    active = bool(trades.any())
    if not active and not volume_complete and (volume_oldest_ts is None or s < volume_oldest_ts):
        # Trade record doesn't reach back this far (capped paging). Fall back to price movement
        # inside the window as evidence that the book was live.
        active = np.unique(seen.price[display]).size > 1
    if not active:
        return drop("stale")
    i_disp = _latest(seen, display)
    if i_disp is not None:
        return make(i_disp, "pm_display")
    i_trade = _latest(seen, trades)
    assert i_trade is not None
    return make(i_trade, "last_trade")


def build(
    markets: pd.DataFrame, prices: pd.DataFrame, coverage: pd.DataFrame
) -> tuple[list[Snapshot], list[Drop]]:
    cov: dict[tuple[str, str], tuple[bool, int | None]] = {
        (str(r["source"]), str(r["market_id"])): (
            bool(r["complete"]),
            None if pd.isna(r["oldest_ts"]) else int(r["oldest_ts"]),
        )
        for r in coverage.to_dict("records")
    }
    grouped = {
        (str(k[0]), str(k[1])): g for k, g in prices.groupby(["source", "market_id"], sort=False)
    }
    empty = PriceSeries.from_frame(prices.iloc[0:0])
    snaps: list[Snapshot] = []
    drops: list[Drop] = []
    for r in markets.to_dict("records"):
        m = Market(
            source=str(r["source"]),
            market_id=str(r["market_id"]),
            event_id=str(r["event_id"]),
            title=str(r["title"]),
            category=str(r["category"]),
            open_ts=int(r["open_ts"]),
            scheduled_close_ts=int(r["scheduled_close_ts"]),
            close_ts=int(r["close_ts"]),
            resolved_ts=None,  # deliberately not passed in: snapshots must not depend on it
            outcome=int(r["outcome"]),
        )
        key = (m.source, m.market_id)
        series = PriceSeries.from_frame(grouped[key]) if key in grouped else empty
        complete, oldest = cov.get(key, (True, None))
        for horizon, seconds in HORIZONS.items():
            out = snapshot_market(m, series, horizon, seconds, complete, oldest)
            if isinstance(out, Snapshot):
                snaps.append(out)
            else:
                drops.append(out)
    return snaps, drops


def summary(snaps: list[Snapshot], drops: list[Drop]) -> pd.DataFrame:
    rows = [{"source": x.source, "horizon": x.horizon, "status": "kept"} for x in snaps]
    rows += [{"source": x.source, "horizon": x.horizon, "status": x.reason} for x in drops]
    df = pd.DataFrame(rows)
    return (
        df.pivot_table(index=["source", "horizon"], columns="status", aggfunc="size", fill_value=0)
        .reset_index()
        .rename_axis(None, axis=1)
    )


def main() -> None:
    con = db.connect()
    markets = con.sql("SELECT * FROM markets").df()
    prices = con.sql("SELECT * FROM prices").df()
    coverage = con.sql("SELECT * FROM volume_coverage").df()
    snaps, drops = build(markets, prices, coverage)
    for src in markets["source"].unique():
        db.replace_source(con, "snapshots", src, [x for x in snaps if x.source == src])
        db.replace_source(con, "snapshot_drops", src, [x for x in drops if x.source == src])
    con.close()
    print(summary(snaps, drops).to_markdown(index=False))


if __name__ == "__main__":
    main()
