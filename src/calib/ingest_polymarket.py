"""Polymarket ingestion: sample closed events by scheduled end date, fetch price history and trades.

Run: ``python -m calib.ingest_polymarket [--target N]``. Safe to interrupt and rerun.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from calib import db
from calib.categories import polymarket_category
from calib.config import (
    CATEGORIES,
    DAY,
    HOUR,
    MAX_MARKETS_PER_EVENT,
    SEED,
    WINDOW_END_TS,
    WINDOW_START_TS,
)
from calib.http_cache import CachedClient
from calib.ingest_kalshi import sample_events
from calib.models import Exclusion, Market, Price, VolumeCoverage

SOURCE = "polymarket"
GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com/v2"
HISTORY_BUCKET_SECONDS = 10800  # 3h: the finest width the API serves for a market's whole life
EVENTS_PAGE = 100
MAX_EVENT_PAGES_PER_WINDOW = 20  # 20 x 100 = Gamma's offset ceiling
MAX_TRADE_PAGES = 50  # 50k trades; beyond this, pre-snapshot volume is only a lower bound
MAX_EVENTS_PER_DAY_PER_CATEGORY = 2


# ---------------------------------------------------------------- parsing (pure, fixture-tested)


def parse_ts(value: str | None) -> int | None:
    """Gamma mixes ISO formats: '2026-03-01T01:00:00Z' and '2026-03-01 07:50:07+00'."""
    if not value:
        return None
    s = value.strip().replace(" ", "T").replace("Z", "+00:00")
    if s[-3] in "+-" and s[-3:].lstrip("+-").isdigit():
        s += ":00"
    return int(datetime.fromisoformat(s).timestamp())


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def event_category(event: dict[str, Any]) -> str:
    tags = [t.get("slug") or "" for t in event.get("tags") or []]
    series = event.get("series") or []
    recurrence = series[0].get("recurrence") if series else None
    return polymarket_category(tags, recurrence)


def parse_market(raw: dict[str, Any], event: dict[str, Any]) -> tuple[Market | Exclusion, str]:
    """One nested Gamma market -> (Market or Exclusion, YES token id)."""
    market_id = raw.get("conditionId") or str(raw.get("id"))
    event_id = str(event.get("id"))
    tokens = _json_list(raw.get("clobTokenIds"))
    yes_token = str(tokens[0]) if tokens else ""

    def excl(reason: str) -> tuple[Exclusion, str]:
        return Exclusion(SOURCE, market_id, event_id, reason), yes_token

    if len(tokens) != 2 or len(_json_list(raw.get("outcomes"))) != 2:
        return excl("not_binary")
    try:
        final = [float(p) for p in _json_list(raw.get("outcomePrices"))]
    except ValueError:
        return excl("unparseable_outcome")
    status = raw.get("umaResolutionStatus") or ""
    if status not in ("resolved", "") or not raw.get("closed"):
        return excl(f"unresolved_or_disputed:{status or 'open'}")
    if sorted(final) != [0.0, 1.0]:
        # A 50/50 split (or anything else) means the question resolved ambiguously.
        return excl("ambiguous_resolution")
    if status == "" and not raw.get("automaticallyResolved"):
        return excl("unresolved_or_disputed:no_status")

    open_ts = parse_ts(raw.get("startDate") or raw.get("createdAt"))
    scheduled = parse_ts(raw.get("endDate"))
    close_ts = parse_ts(raw.get("closedTime")) or scheduled
    if open_ts is None or scheduled is None or close_ts is None:
        return excl("missing_timestamps")

    tags = ",".join(t.get("slug") or "" for t in event.get("tags") or [])
    market = Market(
        source=SOURCE,
        market_id=market_id,
        event_id=event_id,
        title=raw.get("question") or event.get("title") or market_id,
        category=event_category(event),
        open_ts=open_ts,
        scheduled_close_ts=scheduled,
        close_ts=close_ts,
        resolved_ts=parse_ts(raw.get("umaEndDate")) or close_ts,
        outcome=1 if final[0] == 1.0 else 0,
        raw_category=tags,
    )
    return market, yes_token


def parse_history(market_id: str, points: list[dict[str, Any]]) -> list[Price]:
    """Price-history buckets -> rows.

    A bucket is labeled with its start but can reflect trades up to its end, so ``ts`` is set to the
    bucket end: the first moment its value was fully knowable.
    """
    rows = []
    for pt in points:
        width = int(pt.get("resolution_seconds") or HISTORY_BUCKET_SECONDS)
        rows.append(
            Price(
                SOURCE,
                market_id,
                int(pt["timestamp"]) + width,
                float(pt["price"]),
                "pm_display",
                None,
            )
        )
    return sorted(rows, key=lambda r: r.ts)


def parse_trades(market_id: str, trades: list[dict[str, Any]], yes_token: str) -> list[Price]:
    """Trades (any order, either outcome token) -> YES-equivalent last-trade rows with cumulative
    share volume counted from the oldest trade seen."""
    rows = []
    volume_cum = 0.0
    for t in sorted(trades, key=lambda t: (int(t["timestamp"]), t.get("transaction_hash", ""))):
        price = float(t["price"])
        is_yes = str(t.get("token_id")) == yes_token or t.get("outcome_index") == 0
        volume_cum += float(t.get("size") or 0.0)
        rows.append(
            Price(
                SOURCE,
                market_id,
                int(t["timestamp"]),
                price if is_yes else 1.0 - price,
                "last_trade",
                volume_cum,
            )
        )
    return rows


# ---------------------------------------------------------------- fetching


def fetch_day_events(gamma: CachedClient, day_start: int) -> list[dict[str, Any]]:
    """All closed events whose scheduled end falls on one UTC day.

    Gamma rejects offsets past ~2000 and busy days exceed that, so the day is paged in one-hour
    windows. Window bounds are inclusive, so events are de-duplicated by id.
    """
    events: dict[str, dict[str, Any]] = {}
    for hour in range(24):
        lo = datetime.fromtimestamp(day_start + hour * HOUR, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        hi = datetime.fromtimestamp(day_start + (hour + 1) * HOUR - 1, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for page in range(MAX_EVENT_PAGES_PER_WINDOW):
            batch = gamma.get_json(
                "/events",
                {
                    "closed": "true",
                    "end_date_min": lo,
                    "end_date_max": hi,
                    "limit": EVENTS_PAGE,
                    "offset": page * EVENTS_PAGE,
                    "order": "id",
                },
            )
            for e in batch:
                events.setdefault(str(e["id"]), e)
            if len(batch) < EVENTS_PAGE:
                break
    return [events[k] for k in sorted(events)]


def fetch_trades(data: CachedClient, condition_id: str) -> tuple[list[dict[str, Any]], bool]:
    """Page the newest-first trade feed. Returns (trades, complete)."""
    trades: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_TRADE_PAGES):
        params: dict[str, Any] = {"condition": condition_id}
        params.update({"cursor": cursor} if cursor else {"limit": 1000})
        page = data.get_json("/trades", params)
        trades.extend(page.get("data") or [])
        cursor = (page.get("pagination") or {}).get("next_cursor")
        if not cursor:
            return trades, True
    return trades, False


# ---------------------------------------------------------------- pipeline


def run(target: int, n_days: int) -> None:
    gamma = CachedClient("polymarket_gamma", GAMMA_URL, requests_per_second=5)
    data = CachedClient("polymarket_data", DATA_URL, requests_per_second=5)
    rng = random.Random(SEED)

    n_window_days = (WINDOW_END_TS - WINDOW_START_TS) // DAY
    days = sorted(rng.sample(range(n_window_days), min(n_days, n_window_days)))
    per_category = max(1, target // len(CATEGORIES))

    candidates: dict[str, list[Market]] = defaultdict(list)
    frame_exclusions: list[Exclusion] = []  # logged over the whole sampling frame, not just picks
    yes_token: dict[str, str] = {}
    for d in days:
        day_start = WINDOW_START_TS + d * DAY
        day_markets: dict[str, list[Market]] = defaultdict(list)
        for event in fetch_day_events(gamma, day_start):
            for raw in event.get("markets") or []:
                end = parse_ts(raw.get("endDate"))
                if end is None or not WINDOW_START_TS <= end < WINDOW_END_TS:
                    continue
                parsed, token = parse_market(raw, event)
                if isinstance(parsed, Exclusion):
                    frame_exclusions.append(parsed)
                else:
                    yes_token[parsed.market_id] = token
                    day_markets[parsed.category].append(parsed)
        # A few events per category per day spreads the sample across the whole window.
        for cat, ms in day_markets.items():
            candidates[cat] += sample_events(
                ms, rng, MAX_EVENTS_PER_DAY_PER_CATEGORY, MAX_MARKETS_PER_EVENT
            )

    markets: list[Market] = []
    for cat in CATEGORIES:
        pool = candidates.get(cat, [])
        # Shuffle whole events (not markets) so an event's markets stay together.
        events = sorted({m.event_id for m in pool})
        rng.shuffle(events)
        chosen: list[Market] = []
        for e in events:
            if len(chosen) >= per_category:
                break
            chosen += [m for m in pool if m.event_id == e]
        markets += chosen[:per_category]
        print(f"  {cat:<26} {min(len(chosen), per_category):>5} markets (pool {len(pool)})")

    exclusions = list(frame_exclusions)
    prices: list[Price] = []
    coverage: list[VolumeCoverage] = []
    kept: list[Market] = []
    for i, m in enumerate(markets, 1):
        history = data.get_json(
            "/prices-history",
            {
                "token_id": yes_token[m.market_id],
                "interval": "max",
                "bucket_seconds": HISTORY_BUCKET_SECONDS,
            },
        )
        hist_rows = parse_history(m.market_id, history.get("data") or [])
        trades, complete = fetch_trades(data, m.market_id)
        trade_rows = parse_trades(m.market_id, trades, yes_token[m.market_id])
        if not hist_rows and not trade_rows:
            exclusions.append(Exclusion(SOURCE, m.market_id, m.event_id, "no_price_history"))
            continue
        kept.append(m)
        prices += hist_rows + trade_rows
        oldest = trade_rows[0].ts if trade_rows else None
        coverage.append(VolumeCoverage(SOURCE, m.market_id, complete, oldest))
        if i % 100 == 0:
            print(f"  prices {i}/{len(markets)} (cache hits {data.hits}, fetched {data.misses})")

    con = db.connect()
    db.replace_source(con, "markets", SOURCE, kept)
    db.replace_source(con, "exclusions", SOURCE, exclusions)
    db.replace_source(con, "prices", SOURCE, prices)
    db.replace_source(con, "volume_coverage", SOURCE, coverage)
    con.close()
    print(
        f"polymarket: {len(kept)} markets, {len(prices)} price rows, {len(exclusions)} exclusions"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=500, help="approximate markets to sample")
    parser.add_argument("--days", type=int, default=60, help="random days to draw events from")
    args = parser.parse_args()
    run(args.target, args.days)


if __name__ == "__main__":
    main()
