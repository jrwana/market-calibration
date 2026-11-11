"""Parsers against small saved API responses. No network."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from calib import ingest_kalshi as k
from calib import ingest_polymarket as pm
from calib.categories import kalshi_category, polymarket_category
from calib.http_cache import cache_key
from calib.models import Exclusion, Market

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SERIES = {"ticker": "KXSP500REMOVEQ", "category": "Financials", "frequency": "quarterly"}


# ---------------------------------------------------------------- Kalshi


def test_kalshi_market_parses_outcome_and_anchor() -> None:
    raw = fixture("kalshi_markets")["markets"][0]
    m = k.parse_market(raw, SERIES)
    assert isinstance(m, Market)
    assert m.outcome == 1
    assert m.category == "Economics & Finance"
    # Closed early (Sep 8) but was scheduled for Oct 1: the anchor must be the schedule.
    assert m.scheduled_close_ts == k.parse_ts("2026-10-01T14:00:00Z")
    assert m.close_ts == k.parse_ts("2026-09-08T19:40:44Z")
    assert m.close_ts < m.scheduled_close_ts


def test_kalshi_no_outcome() -> None:
    m = k.parse_market(fixture("kalshi_markets")["markets"][1], SERIES)
    assert isinstance(m, Market) and m.outcome == 0


@pytest.mark.parametrize(("index", "reason"), [(2, "parlay"), (3, "unresolved_or_void:blank")])
def test_kalshi_exclusions(index: int, reason: str) -> None:
    x = k.parse_market(fixture("kalshi_markets")["markets"][index], SERIES)
    assert isinstance(x, Exclusion)
    assert x.reason == reason


def test_kalshi_scalar_excluded() -> None:
    raw = dict(fixture("kalshi_markets")["markets"][0], market_type="scalar")
    x = k.parse_market(raw, SERIES)
    assert isinstance(x, Exclusion) and x.reason == "not_binary"


def test_kalshi_live_candles_mid_and_spread() -> None:
    rows = k.parse_candles("T", fixture("kalshi_candles_live")["candlesticks"])
    first = rows[0]
    assert first.price_type == "mid"
    assert first.price == pytest.approx((0.01 + 0.91) / 2)
    assert first.spread == pytest.approx(0.90)
    assert all(r.ts == int(r.ts) for r in rows)


def test_kalshi_historical_candle_field_names() -> None:
    rows = k.parse_candles("T", fixture("kalshi_candles_historical")["candlesticks"])
    # Candle 1 has ask = 1.00 (no real offer), so no midpoint is emitted for it.
    assert rows[0].ts == 1737050400
    assert rows[0].price == pytest.approx(0.77)
    trades = [r for r in rows if r.price_type == "last_trade"]
    assert trades[0].price == pytest.approx(0.87)
    assert trades[0].volume_cum == pytest.approx(9.0)


def test_kalshi_candles_sorted_and_volume_monotone() -> None:
    candles = list(reversed(fixture("kalshi_candles_historical")["candlesticks"]))
    rows = k.parse_candles("T", candles)
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
    vols = [r.volume_cum for r in rows if r.volume_cum is not None]
    assert vols == sorted(vols)


def test_kalshi_short_dated_override() -> None:
    assert kalshi_category("Crypto", "hourly") == "Short-dated recurring"
    assert kalshi_category("Sports", "daily") == "Sports"
    assert kalshi_category("Exotics", "custom") == "Other"


# ---------------------------------------------------------------- Polymarket


def test_polymarket_market_and_yes_token() -> None:
    event = fixture("polymarket_event")
    m, token = pm.parse_market(event["markets"][0], event)
    assert isinstance(m, Market)
    assert m.category == "Sports"  # a daily series, but sports are never "short-dated recurring"
    assert token == json.loads(event["markets"][0]["clobTokenIds"])[0]
    assert m.outcome == (1 if json.loads(event["markets"][0]["outcomePrices"])[0] == "1" else 0)


def test_polymarket_fifty_fifty_excluded() -> None:
    event = fixture("polymarket_event")
    x, _ = pm.parse_market(event["markets"][1], event)
    assert isinstance(x, Exclusion) and x.reason == "ambiguous_resolution"


def test_polymarket_disputed_excluded() -> None:
    event = fixture("polymarket_event")
    x, _ = pm.parse_market(event["markets"][2], event)
    assert isinstance(x, Exclusion) and x.reason.startswith("unresolved_or_disputed")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-03-01T01:00:00Z", 1772326800),
        ("2026-03-01 07:50:07+00", 1772351407),
        ("2026-03-01T01:00:00.123Z", 1772326800),
    ],
)
def test_polymarket_timestamp_formats(text: str, expected: int) -> None:
    assert pm.parse_ts(text) == expected


def test_polymarket_history_uses_bucket_end() -> None:
    points = fixture("polymarket_history")["data"]
    rows = pm.parse_history("M", points)
    assert rows[0].ts == points[0]["timestamp"] + points[0]["resolution_seconds"]
    assert all(r.price_type == "pm_display" for r in rows)


def test_polymarket_trades_yes_equivalent_and_cumulative() -> None:
    trades = fixture("polymarket_trades")["data"]
    yes_token = next(t["token_id"] for t in trades if t["outcome_index"] == 0)
    rows = pm.parse_trades("M", trades, yes_token)
    by_ts = {t["timestamp"]: t for t in trades}
    for r in rows:
        t = by_ts[r.ts]
        expected = t["price"] if t["outcome_index"] == 0 else 1 - t["price"]
        assert r.price == pytest.approx(expected)
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
    assert rows[-1].volume_cum == pytest.approx(sum(t["size"] for t in trades))


def test_polymarket_categories() -> None:
    assert polymarket_category(["crypto", "up-or-down"], None) == "Short-dated recurring"
    assert polymarket_category(["politics", "mention-markets"], None) == "Culture & Entertainment"
    assert polymarket_category(["weather", "science"], None) == "Weather & Climate"
    assert polymarket_category(["nba", "sports"], "daily") == "Sports"


# ---------------------------------------------------------------- sampling and cache


def _market(event: str, i: int) -> Market:
    return Market("s", f"{event}-{i}", event, "t", "c", 0, 1, 1, None, 0)


def test_sample_events_caps() -> None:
    markets = [_market(f"E{e}", i) for e in range(10) for i in range(8)]
    chosen = k.sample_events(markets, random.Random(0), max_events=3, max_per_event=2)
    assert len({m.event_id for m in chosen}) == 3
    assert len(chosen) == 6


def test_sample_events_is_deterministic() -> None:
    markets = [_market(f"E{e}", i) for e in range(10) for i in range(8)]
    a = k.sample_events(markets, random.Random(7), 3, 2)
    b = k.sample_events(list(reversed(markets)), random.Random(7), 3, 2)
    assert a == b


def test_cache_key_ignores_param_order() -> None:
    assert cache_key("/x", {"a": 1, "b": 2}) == cache_key("/x", {"b": 2, "a": 1})
    assert cache_key("/x", {"a": 1}) != cache_key("/x", {"a": 2})
