# Data sources: what the APIs actually do

Checked against live endpoints in November 2026. Where docs and behavior disagreed, I went with behavior
and noted it here.

## Kalshi

Base: `https://api.elections.kalshi.com/trade-api/v2`. Every read endpoint used here is public (no auth).

| Need | Endpoint | Notes |
|---|---|---|
| Market universe | `GET /series` | ~16k series, each with `category` and `frequency`. This is the sampling frame. |
| Settled markets (recent) | `GET /markets?series_ticker=…&status=settled` | |
| Settled markets (older) | `GET /historical/markets?series_ticker=…` | Markets settled before `GET /historical/cutoff → market_settled_ts` live only here. `min_close_ts`/`max_close_ts` are **ignored** on this route, so date filtering happens locally. |
| Prices | `GET /series/{s}/markets/{t}/candlesticks`, `GET /historical/markets/{t}/candlesticks` | OHLC for trade price **and** yes bid/ask, plus volume. The historical route uses different field names (`close` vs `close_dollars`, `volume` vs `volume_fp`); the parser handles both. |

**Outcome:** `result ∈ {yes, no}`. Anything else (empty, voided) is excluded.

**Timestamps that matter:**
- `expected_expiration_time`: when the event is *scheduled* to be decided. This anchors the horizons.
- `close_time`: when trading actually stopped. For `can_close_early` markets this moves earlier once the outcome is known.
- `settlement_ts`: when the payout happened. Never used to place a snapshot (see "Why not anchor to resolution").

**Trap:** sorting settled markets by recency returns almost nothing but auto-generated multi-leg parlays
(`KXMVE…` tickers, `mve_collection_ticker` set). Sampling through `/series` avoids them, and they are
excluded explicitly anyway.

**Rate limits:** the Basic tier is 200 tokens/s at 10 tokens per read (~20 reads/s). The pipeline stays well under that.

## Polymarket

| Need | Endpoint | Notes |
|---|---|---|
| Market universe | Gamma `GET /events?closed=true&end_date_min=…&end_date_max=…` | Pages of at most 100. Events carry `tags` (used for category) and `series[].recurrence`. |
| Prices | Data API `GET /v2/prices-history?token_id=…&interval=max&bucket_seconds=10800` | Whole market life at 3-hour resolution in **one call**. Finer buckets only cover 15-day windows. |
| Volume | Data API `GET /v2/trades?condition=…` | Newest-first, cursor-paged, max 1000 per page. `start`/`end` are **ignored** unless filtering by wallet, and there is no ascending sort. |

The Data API v1 (`/trades`, `clob…/prices-history` docs) is retired and returns `410`.

**Outcome:** `outcomePrices` exactly `["1","0"]` or `["0","1"]` with `umaResolutionStatus = resolved`.
Anything else (50/50 splits, unresolved, disputed) is excluded. "YES" is the first outcome token, which for
non-Yes/No markets (e.g., team names) is an arbitrary but fixed side.

**Prices aren't quotes:** the history series is Polymarket's displayed price (midpoint, or last trade when the
spread is wide), and no historical bid/ask is available. Rows are labeled `pm_display` and not `mid`.

**Leakage trap:** a 3-hour bucket is labeled with its *start* time but may reflect trades up to its end.
The pipeline sets `price_ts = bucket_start + bucket_width`, so a bucket can only be used once it has fully closed.

**Volume cost:** pre-snapshot volume needs paging backward from the latest trade. That's cheap for typical
markets but unbounded for the largest, so paging is capped. When the cap is hit, the trades seen still give a
*lower bound* on pre-snapshot volume. If that bound clears the top liquidity threshold the bucket is known;
otherwise the market lands in an explicit `unknown` bucket. It is never assigned from final volume.

**Rate limits:** not published. `429` responses carry `Retry-After`, which the client honors.

## Why not anchor to resolution

"Will X happen by Dec 31?" resolves YES the moment X happens and NO only at the deadline, and Polymarket adds
a dispute window after the event. A snapshot placed at "resolution − 1 day" therefore depends on the outcome
and can land after the answer is public. Snapshots are instead placed at *scheduled close − horizon*, and a
market contributes to a horizon only if it was still trading then. Whether a market is open at time t is
known at t, so this conditioning doesn't look ahead.
