"""Map each platform's labels onto one shared category list (see config.CATEGORIES)."""

from __future__ import annotations

from collections.abc import Iterable

KALSHI_CATEGORY_MAP: dict[str, str] = {
    "Politics": "Politics & World",
    "Elections": "Politics & World",
    "World": "Politics & World",
    "Sports": "Sports",
    "Financials": "Economics & Finance",
    "Economics": "Economics & Finance",
    "Companies": "Economics & Finance",
    "Commodities": "Economics & Finance",
    "Business": "Economics & Finance",
    "Crypto": "Crypto",
    "Entertainment": "Culture & Entertainment",
    "Culture": "Culture & Entertainment",
    "Social": "Culture & Entertainment",
    "Mentions": "Culture & Entertainment",
    "Climate and Weather": "Weather & Climate",
    "Science and Technology": "Science & Tech",
    "Health": "Science & Tech",
}

# Recurring markets that settle within a day (crypto/stock/weather price brackets). They are
# numerous, priced near 0 or 1, and would dominate pooled results, so they get their own category.
SHORT_FREQUENCIES = frozenset({"fifteen_min", "hourly", "daily", "15m", "5m", "1h", "4h"})
SHORT_TAGS = frozenset({"up-or-down", "15m", "5m", "1h", "4h", "hourly", "daily"})
# "Will X say Y" markets: Kalshi files them under Mentions; keep both platforms consistent.
MENTION_TAGS = frozenset({"mention-markets", "mentions"})

# Polymarket tags, checked in priority order: the first group with a matching tag wins.
POLYMARKET_TAG_PRIORITY: tuple[tuple[str, frozenset[str]], ...] = (
    ("Crypto", frozenset({"crypto", "bitcoin", "ethereum", "solana", "crypto-prices", "xrp"})),
    (
        "Sports",
        frozenset(
            {
                "sports",
                "nba",
                "nfl",
                "mlb",
                "nhl",
                "soccer",
                "tennis",
                "esports",
                "games",
                "cricket",
                "golf",
                "ufc",
                "f1",
                "boxing",
                "mma",
            }
        ),
    ),
    ("Weather & Climate", frozenset({"weather", "climate", "temperature", "hurricanes"})),
    ("Science & Tech", frozenset({"science", "tech", "ai", "space", "health", "big-tech"})),
    (
        "Economics & Finance",
        frozenset(
            {
                "economy",
                "finance",
                "fed",
                "equities",
                "stocks",
                "business",
                "economics",
                "inflation",
                "commodities",
                "fed-rates",
                "earnings",
            }
        ),
    ),
    (
        "Politics & World",
        frozenset(
            {
                "politics",
                "elections",
                "world",
                "geopolitics",
                "us-politics",
                "global-elections",
                "trump",
                "middle-east",
                "ukraine",
            }
        ),
    ),
    (
        "Culture & Entertainment",
        frozenset(
            {
                "culture",
                "pop-culture",
                "movies",
                "music",
                "awards",
                "entertainment",
                "celebrities",
                "tv",
                "mentions",
            }
        ),
    ),
)


def kalshi_category(raw_category: str, frequency: str) -> str:
    unified = KALSHI_CATEGORY_MAP.get(raw_category, "Other")
    if frequency in SHORT_FREQUENCIES and unified != "Sports":
        return "Short-dated recurring"
    return unified


def polymarket_category(tag_slugs: Iterable[str], recurrence: str | None) -> str:
    slugs = {s.lower() for s in tag_slugs}
    if slugs & MENTION_TAGS:
        return "Culture & Entertainment"
    unified = "Other"
    for name, group in POLYMARKET_TAG_PRIORITY:
        if slugs & group:
            unified = name
            break
    short = recurrence in SHORT_FREQUENCIES or bool(slugs & SHORT_TAGS)
    if short and unified != "Sports":
        return "Short-dated recurring"
    return unified
