"""Project-wide constants. Methodology knobs live here so they are easy to find and cite."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "calib.duckdb"
REPORTS_DIR = ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
APP_DATA_DIR = ROOT / "app" / "data"


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp())


# Markets whose *scheduled* close falls in this window. Fixed dates, not "now minus 24 months",
# so a rerun next month samples the same universe.
WINDOW_START_TS = _ts("2024-11-01")
WINDOW_END_TS = _ts("2026-11-01")

HOUR = 3600
DAY = 86400

HORIZONS: dict[str, int] = {"T-30d": 30 * DAY, "T-7d": 7 * DAY, "T-1d": 1 * DAY}

# A price older than this at snapshot time is stale; the market is dropped for that horizon.
STALE_SECONDS = 48 * HOUR
# A Kalshi midpoint is only trusted when the book is this tight; otherwise fall back to last trade.
MAX_SPREAD = 0.10
# Log loss clipping.
CLIP_EPS = 1e-4

# Liquidity buckets on contracts traded *before* the snapshot (each contract pays $1).
LIQUIDITY_EDGES: tuple[float, ...] = (1_000.0, 10_000.0, 100_000.0)
LIQUIDITY_LABELS: tuple[str, ...] = ("<1k", "1k-10k", "10k-100k", ">=100k")

# Sampling.
SEED = 20261111
MAX_MARKETS_PER_EVENT = 3
MAX_EVENTS_PER_SERIES = 2

# Unified categories shared by both platforms.
CATEGORIES: tuple[str, ...] = (
    "Politics & World",
    "Sports",
    "Economics & Finance",
    "Crypto",
    "Culture & Entertainment",
    "Weather & Climate",
    "Science & Tech",
    "Short-dated recurring",
)
