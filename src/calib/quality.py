"""Data-quality report: what's in the sample, what was excluded and why, and price coverage.

Run: ``python -m calib.quality``. Also writes ``reports/data_quality.md``.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from calib import db
from calib.config import HORIZONS, REPORTS_DIR, STALE_SECONDS


def sections(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    out["Markets by platform and outcome"] = con.sql("""
        SELECT source, count(*) AS markets, count(DISTINCT event_id) AS events,
               sum(outcome) AS yes, count(*) - sum(outcome) AS no,
               round(avg(outcome), 3) AS base_rate
        FROM markets GROUP BY source ORDER BY source
    """).df()
    out["Markets by category"] = con.sql("""
        PIVOT (SELECT source, category FROM markets)
        ON source USING count(*) GROUP BY category ORDER BY category
    """).df()
    out["Exclusions by reason (whole sampling frame)"] = con.sql("""
        SELECT source, split_part(reason, ':', 1) AS reason, count(*) AS markets
        FROM exclusions GROUP BY ALL ORDER BY source, markets DESC
    """).df()
    out["Price-history coverage"] = con.sql("""
        SELECT p.source, price_type, count(DISTINCT p.market_id) AS markets,
               count(*) AS rows, round(count(*) / count(DISTINCT p.market_id), 1) AS rows_per_market
        FROM prices p GROUP BY ALL ORDER BY ALL
    """).df()
    out["Volume coverage"] = con.sql("""
        SELECT source, count(*) AS markets, sum(complete::INT) AS complete,
               count(*) - sum(complete::INT) AS capped
        FROM volume_coverage GROUP BY source ORDER BY source
    """).df()

    # Preview of Phase 2: share of markets that were open at each horizon and had *any* price
    # observation in the 48h before it. The snapshot step applies the full rules.
    parts = []
    for name, seconds in HORIZONS.items():
        parts.append(f"""
            SELECT m.source, '{name}' AS horizon, count(*) AS markets,
                   count(*) FILTER (WHERE m.open_ts <= m.scheduled_close_ts - {seconds}
                                    AND m.close_ts > m.scheduled_close_ts - {seconds}) AS open_then,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM prices p
                       WHERE p.source = m.source AND p.market_id = m.market_id
                         AND p.ts <= m.scheduled_close_ts - {seconds}
                         AND p.ts > m.scheduled_close_ts - {seconds} - {STALE_SECONDS}
                         AND m.close_ts > m.scheduled_close_ts - {seconds})) AS fresh_price
            FROM markets m GROUP BY m.source""")
    out["Horizon coverage preview"] = con.sql(
        " UNION ALL ".join(parts) + " ORDER BY source, horizon"
    ).df()
    return out


def render(tables: dict[str, pd.DataFrame]) -> str:
    lines = ["# Data-quality report", ""]
    for title, df in tables.items():
        lines += [f"## {title}", "", df.to_markdown(index=False), ""]
    return "\n".join(lines)


def main() -> None:
    con = db.connect()
    text = render(sections(con))
    con.close()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "data_quality.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
