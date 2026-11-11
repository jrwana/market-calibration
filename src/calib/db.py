"""DuckDB schema and writers. Each ingest run replaces its own source's rows, so reruns are idempotent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from calib.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    source VARCHAR, market_id VARCHAR, event_id VARCHAR, title VARCHAR, category VARCHAR,
    open_ts BIGINT, scheduled_close_ts BIGINT, close_ts BIGINT, resolved_ts BIGINT,
    outcome TINYINT, raw_category VARCHAR
);
CREATE TABLE IF NOT EXISTS exclusions (
    source VARCHAR, market_id VARCHAR, event_id VARCHAR, reason VARCHAR
);
CREATE TABLE IF NOT EXISTS prices (
    source VARCHAR, market_id VARCHAR, ts BIGINT, price DOUBLE, price_type VARCHAR,
    volume_cum DOUBLE, spread DOUBLE
);
CREATE TABLE IF NOT EXISTS volume_coverage (
    source VARCHAR, market_id VARCHAR, complete BOOLEAN, oldest_ts BIGINT
);
CREATE TABLE IF NOT EXISTS snapshots (
    source VARCHAR, market_id VARCHAR, horizon VARCHAR, snapshot_ts BIGINT, price_ts BIGINT,
    p DOUBLE, price_type VARCHAR, volume_before DOUBLE, liquidity VARCHAR
);
CREATE TABLE IF NOT EXISTS snapshot_drops (
    source VARCHAR, market_id VARCHAR, horizon VARCHAR, reason VARCHAR
);
"""


def connect(path: Path = DB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def to_frame(rows: Sequence[Any]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in rows])


def replace_source(
    con: duckdb.DuckDBPyConnection, table: str, source: str, rows: Sequence[Any]
) -> int:
    """Delete this source's rows from ``table`` and insert ``rows`` in one transaction."""
    con.execute("BEGIN")
    con.execute(f"DELETE FROM {table} WHERE source = ?", [source])
    if rows:
        df = to_frame(rows)  # noqa: F841 - referenced by name in the SQL below
        cols = ", ".join(df.columns)
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")
    con.execute("COMMIT")
    return len(rows)
