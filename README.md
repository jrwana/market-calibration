# Are prediction markets calibrated?

When Kalshi or Polymarket prices an event at 70%, does it happen about 70% of the time? I checked
resolved binary markets from both platforms, priced at fixed horizons before their scheduled close,
with standard forecast-evaluation tools and uncertainty that accounts for correlated markets.

<!-- RESULTS -->

## Why this question

Ben Recht recently argued that prediction markets ["don't produce probabilities in any reasonable
sense"](https://www.argmin.net/p/no-one-buys-corn-in-the-corn-market). Much of that argument is about
incentives and regulation, which data can't settle. One part can be checked: if prices behave like
probabilities, priced-at-p events should happen about p of the time, across platforms, categories,
liquidity levels and horizons. That's necessary for "markets produce probabilities," not sufficient:
a calibrated market can still be useless for decisions if it only becomes sharp the day before.

## Method

| Step | What happens |
|---|---|
| Sample | Resolved binary markets whose *scheduled* close falls between 2024-11-01 and 2026-11-01. Kalshi is sampled through its `/series` catalog, Polymarket through randomly drawn days of closed events. Both are stratified by a shared 8-category scheme, with at most 3 markets per event so threshold ladders can't dominate. Selection never looks at volume. |
| Snapshot | Price at **T-30d, T-7d and T-1d before the scheduled close**. Kalshi: bid/ask midpoint when the book is ≤10¢ wide, otherwise last trade. Polymarket: its displayed price (it publishes no historical quotes), gated on a real trade in the prior 48h. |
| Score | Brier score, log loss (clipped to [1e-4, 1−1e-4]), reliability curves with 10 equal-count bins, ECE, the Murphy decomposition (with the Stephenson within-bin terms so it sums exactly), and Brier skill score against the group's base rate. |
| Uncertainty | 95% percentile bootstrap that resamples **events**, not markets. Ten candidates in one election aren't ten independent forecasts. |
| Robustness | Same metrics on (a) the fixed population of markets priced at all three horizons, and (b) one randomly chosen market per event. Breakdowns by category, by pre-snapshot volume, and by price type. |

Details of each API (and where it disagreed with its docs) are in
[docs/data_sources.md](docs/data_sources.md).

## Leakage safeguards

Look-ahead bias is the easiest way to make markets look better than they are, so each rule below is
enforced in code and covered by a test.

- **Horizons anchor to the scheduled close, never resolution.** "By Dec 31" markets resolve YES early
  and NO at the deadline, so "resolution − 1 day" depends on the outcome. The snapshot code never
  receives the resolution timestamp, and a property test checks that changing it changes nothing.
- **Only data at or before the snapshot is visible**, volume included: liquidity buckets use contracts
  traded *before* the snapshot, never final volume. A Hypothesis test appends arbitrary future rows
  (with absurd volume) and checks that no snapshot changes.
- **Polymarket's 3-hour price buckets are timestamped at their end**, because a bucket labeled 12:00
  can include trades up to 15:00.
- **Stale prices are dropped and counted, never forward-filled**: no trade or quote in the 48h before
  the snapshot means no snapshot.
- **Capped volume stays unknown.** When Polymarket's trade history is too long to page fully, the
  market gets an explicit `unknown` liquidity bucket unless the partial count already proves it's in
  the top bucket.
- **A test runs over the real snapshot table** and checks every row for `price_ts ≤ snapshot_ts` and a
  recomputed `volume_before`.

## Limitations

- **Prices aren't free probabilities.** Fees and the bid/ask spread mean a 3¢ contract can be
  "overpriced" without being exploitable. Calibration here measures the price, not a trading edge.
- **Correlated markets.** Clustering by event handles markets inside one event; it doesn't handle
  correlation *across* events (every Fed-adjacent market moving on one CPI print).
- **Sample selection.** Stratifying by category means pooled results describe that mix, not the
  platforms' actual volume-weighted activity. Short-dated recurring brackets are reported as their
  own category because they'd otherwise swamp the pool.
- **Platform asymmetry.** Kalshi snapshots can use quotes; Polymarket's can't (no historical book).
  Results by price type are in the scorecard.
- **Resolution is taken as given.** Markets that resolved ambiguously (50/50) or were voided are
  excluded and counted, not adjudicated.

## Reproduce

```bash
uv sync --all-extras
make data        # ingest both platforms (cached, resumable) + data-quality report
make snapshots   # leak-free snapshots
make eval        # reports/scorecard.json and reports/figures/
make test        # parsers, leakage properties, metric properties
make app         # Streamlit explorer at localhost:8501
```

The raw API cache (`data/`) isn't committed. `make data` rebuilds it, and every response is cached, so
a rerun makes no new requests. The dashboard reads only `app/data/snapshots.parquet`, so it also
runs from a fresh clone (`docker build -t calib . && docker run -p 8501:8501 calib`).

## Layout

```
src/calib/
  ingest_kalshi.py, ingest_polymarket.py   # fetch + pure parsers -> DuckDB
  snapshots.py                             # leak-free snapshot rules
  metrics.py                               # arrays in, numbers out
  report.py                                # scorecard + figures
  llm_forecast.py                          # optional LLM baseline
app/app.py                                 # dashboard
tests/                                     # fixtures, leakage + metric property tests
```

## Status

- [x] API survey
- [x] Data pipeline
- [x] Leak-free snapshots and leakage tests
- [x] Metrics and scorecard
- [x] Dashboard
- [ ] Write-up of results
- [ ] LLM baseline run
