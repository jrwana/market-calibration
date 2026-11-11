"""Normalized row types shared by both platforms. All timestamps are UTC epoch seconds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    source: str
    market_id: str
    event_id: str
    title: str
    category: str
    open_ts: int
    scheduled_close_ts: int  # when the event was scheduled to be decided; anchors the horizons
    close_ts: int  # when trading actually stopped (earlier than scheduled if closed early)
    resolved_ts: int | None  # informational only; never used to place a snapshot
    outcome: int  # 1 = YES, 0 = NO
    raw_category: str = ""


@dataclass(frozen=True)
class Exclusion:
    source: str
    market_id: str
    event_id: str
    reason: str


@dataclass(frozen=True)
class Price:
    source: str
    market_id: str
    ts: int  # the earliest moment this observation was knowable
    price: float  # implied YES probability
    price_type: str  # "mid", "last_trade", or "pm_display"
    volume_cum: float | None  # contracts traded up to and including ts, where known
    spread: float | None = None


@dataclass(frozen=True)
class VolumeCoverage:
    """How far back the volume record reaches. Kalshi candles are always complete; Polymarket trade
    paging can be capped, in which case only trades at or after ``oldest_ts`` were seen."""

    source: str
    market_id: str
    complete: bool
    oldest_ts: int | None
