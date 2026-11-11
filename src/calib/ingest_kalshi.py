"""Kalshi ingestion: sample settled binary markets through /series, fetch hourly candles, normalize.

Run: ``python -m calib.ingest_kalshi [--target N]``. Safe to interrupt and rerun.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from calib import db
from calib.categories import kalshi_category
from calib.config import (
    CATEGORIES,
    HOUR,
    MAX_EVENTS_PER_SERIES,
    MAX_MARKETS_PER_EVENT,
    SEED,
    WINDOW_END_TS,
    WINDOW_START_TS,
)
from calib.http_cache import CachedClient
from calib.models import Exclusion, Market, Price, VolumeCoverage

SOURCE = "kalshi"
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MAX_CANDLES_PER_REQUEST = 5000
MAX_PAGES_PER_LISTING = 3
MAX_SERIES_PER_CATEGORY = 2500


# ---------------------------------------------------------------- parsing (pure, fixture-tested)


def parse_ts(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def is_parlay(raw: dict[str, Any]) -> bool:
    return bool(raw.get("mve_collection_ticker")) or str(raw.get("event_ticker", "")).startswith(
        "KXMVE"
    )


def parse_market(raw: dict[str, Any], series: dict[str, Any]) -> Market | Exclusion:
    """One raw market -> a clean Market, or an Exclusion with the reason."""
    ticker = raw["ticker"]
    event = raw.get("event_ticker", "")
    if is_parlay(raw):
        return Exclusion(SOURCE, ticker, event, "parlay")
    if raw.get("market_type", "binary") != "binary":
        return Exclusion(SOURCE, ticker, event, "not_binary")
    result = (raw.get("result") or "").lower()
    if result not in ("yes", "no"):
        return Exclusion(SOURCE, ticker, event, f"unresolved_or_void:{result or 'blank'}")

    open_ts = parse_ts(raw.get("open_time"))
    close_ts = parse_ts(raw.get("close_time"))
    scheduled = parse_ts(raw.get("expected_expiration_time")) or close_ts
    if open_ts is None or close_ts is None or scheduled is None:
        return Exclusion(SOURCE, ticker, event, "missing_timestamps")

    raw_category = series.get("category") or ""
    return Market(
        source=SOURCE,
        market_id=ticker,
        event_id=event,
        title=raw.get("title") or ticker,
        category=kalshi_category(raw_category, series.get("frequency") or ""),
        open_ts=open_ts,
        scheduled_close_ts=scheduled,
        close_ts=close_ts,
        resolved_ts=parse_ts(raw.get("settlement_ts")),
        outcome=1 if result == "yes" else 0,
        raw_category=raw_category,
    )


def in_window(raw: dict[str, Any]) -> bool:
    """Is the market's scheduled close inside the study window? Applied before parsing so exclusion
    counts describe the same frame the sample is drawn from."""
    ts = parse_ts(raw.get("expected_expiration_time")) or parse_ts(raw.get("close_time"))
    return ts is not None and WINDOW_START_TS <= ts < WINDOW_END_TS


def _ohlc(block: dict[str, Any] | None, field: str) -> float | None:
    """Live candles use ``close_dollars``; historical ones use ``close``."""
    if not block:
        return None
    return _num(block.get(f"{field}_dollars", block.get(field)))


def parse_candles(market_id: str, candles: list[dict[str, Any]]) -> list[Price]:
    """Hourly candles -> price rows. ``ts`` is the candle's *end*, the first moment it was knowable.

    Each candle can yield a midpoint row (when both sides of the book are present) and a last-trade
    row (when something traded in the period). Both carry cumulative volume through ``ts``.
    """
    rows: list[Price] = []
    volume_cum = 0.0
    for c in sorted(candles, key=lambda c: int(c["end_period_ts"])):
        ts = int(c["end_period_ts"])
        volume_cum += _num(c.get("volume_fp", c.get("volume"))) or 0.0
        bid = _ohlc(c.get("yes_bid"), "close")
        ask = _ohlc(c.get("yes_ask"), "close")
        if bid is not None and ask is not None and 0.0 < bid <= ask < 1.0:
            rows.append(
                Price(
                    SOURCE, market_id, ts, (bid + ask) / 2, "mid", volume_cum, round(ask - bid, 4)
                )
            )
        trade = _ohlc(c.get("price"), "close")
        if trade is not None:
            rows.append(Price(SOURCE, market_id, ts, trade, "last_trade", volume_cum))
    return rows


def sample_events(
    markets: list[Market], rng: random.Random, max_events: int, max_per_event: int
) -> list[Market]:
    """Pick up to ``max_events`` events at random, then up to ``max_per_event`` markets in each.

    Capping per event keeps threshold ladders (20+ near-duplicate markets) from dominating, and
    random choice (not volume) avoids selecting on liquidity.
    """
    by_event: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        by_event[m.event_id].append(m)
    events = sorted(by_event)
    rng.shuffle(events)
    chosen: list[Market] = []
    for e in events[:max_events]:
        group = sorted(by_event[e], key=lambda m: m.market_id)
        rng.shuffle(group)
        chosen.extend(group[:max_per_event])
    return chosen


# ---------------------------------------------------------------- fetching


def iter_pages(client: CachedClient, path: str, params: dict[str, Any], key: str) -> Iterator[Any]:
    cursor: str | None = None
    for _ in range(MAX_PAGES_PER_LISTING):
        page = client.get_json(path, {**params, **({"cursor": cursor} if cursor else {})})
        yield from page.get(key, [])
        cursor = page.get("cursor")
        if not cursor:
            break


def fetch_series_markets(client: CachedClient, series_ticker: str) -> list[dict[str, Any]]:
    """Settled markets for a series from both the live and the historical tier."""
    live = iter_pages(
        client,
        "/markets",
        {"series_ticker": series_ticker, "status": "settled", "limit": 1000},
        "markets",
    )
    hist = iter_pages(
        client, "/historical/markets", {"series_ticker": series_ticker, "limit": 1000}, "markets"
    )
    seen: dict[str, dict[str, Any]] = {}
    for raw in [*live, *hist]:
        seen.setdefault(raw["ticker"], raw)
    return list(seen.values())


def fetch_candles(
    client: CachedClient, market: Market, series_ticker: str, historical: bool
) -> list[dict[str, Any]]:
    path = (
        f"/historical/markets/{market.market_id}/candlesticks"
        if historical
        else f"/series/{series_ticker}/markets/{market.market_id}/candlesticks"
    )
    candles: list[dict[str, Any]] = []
    start = market.open_ts
    end = min(market.close_ts, market.scheduled_close_ts) + HOUR
    while start < end:
        chunk_end = min(end, start + MAX_CANDLES_PER_REQUEST * HOUR)
        page = client.get_json(
            path, {"start_ts": start, "end_ts": chunk_end, "period_interval": 60}
        )
        candles.extend(page.get("candlesticks") or [])
        start = chunk_end
    return candles


# ---------------------------------------------------------------- pipeline


def run(target: int) -> None:
    client = CachedClient(SOURCE, BASE_URL, requests_per_second=5)
    rng = random.Random(SEED)
    cutoff = parse_ts(client.get_json("/historical/cutoff")["market_settled_ts"]) or 0
    all_series = client.get_json("/series")["series"]

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sorted(all_series, key=lambda s: s["ticker"]):
        cat = kalshi_category(s.get("category") or "", s.get("frequency") or "")
        if cat in CATEGORIES:
            by_category[cat].append(s)

    per_category = max(1, target // len(CATEGORIES))
    markets: list[Market] = []
    exclusions: list[Exclusion] = []
    series_of: dict[str, str] = {}
    for cat in CATEGORIES:
        pool = by_category.get(cat, [])
        rng.shuffle(pool)
        got = 0
        for series in pool[:MAX_SERIES_PER_CATEGORY]:
            if got >= per_category:
                break
            raws = [r for r in fetch_series_markets(client, series["ticker"]) if in_window(r)]
            parsed = [parse_market(r, series) for r in raws]
            exclusions += [p for p in parsed if isinstance(p, Exclusion)]
            clean = [p for p in parsed if isinstance(p, Market)]
            chosen = sample_events(clean, rng, MAX_EVENTS_PER_SERIES, MAX_MARKETS_PER_EVENT)
            chosen = chosen[: per_category - got]
            for m in chosen:
                series_of[m.market_id] = series["ticker"]
            markets += chosen
            got += len(chosen)
        print(f"  {cat:<26} {got:>5} markets")

    prices: list[Price] = []
    kept: list[Market] = []
    for i, m in enumerate(markets, 1):
        historical = (m.resolved_ts or m.close_ts) < cutoff
        rows = parse_candles(
            m.market_id, fetch_candles(client, m, series_of[m.market_id], historical)
        )
        if rows:
            kept.append(m)
            prices += rows
        else:
            exclusions.append(Exclusion(SOURCE, m.market_id, m.event_id, "no_price_history"))
        if i % 100 == 0:
            print(
                f"  candles {i}/{len(markets)} (cache hits {client.hits}, fetched {client.misses})"
            )

    coverage = [VolumeCoverage(SOURCE, m.market_id, True, m.open_ts) for m in kept]
    con = db.connect()
    db.replace_source(con, "markets", SOURCE, kept)
    db.replace_source(con, "exclusions", SOURCE, exclusions)
    db.replace_source(con, "prices", SOURCE, prices)
    db.replace_source(con, "volume_coverage", SOURCE, coverage)
    con.close()
    print(f"kalshi: {len(kept)} markets, {len(prices)} price rows, {len(exclusions)} exclusions")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=500, help="approximate markets to sample")
    run(parser.parse_args().target)


if __name__ == "__main__":
    main()
