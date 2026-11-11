"""LLM forecaster baseline: the model sees only the question and the scheduled close date.

Leakage rule: only markets that closed AND resolved after the model's training cutoff are eligible;
otherwise the model may simply remember the answer.

Reproducibility: current Claude models don't accept a temperature setting, so determinism comes from
a frozen prompt (versioned) and a permanent cache: every response is stored under
``data/raw/llm/<model>/<prompt_version>/`` and never requested twice.

Run: ``python -m calib.llm_forecast [--limit N]`` (needs ANTHROPIC_API_KEY in .env; costs money).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from calib import db
from calib.config import RAW_DIR

MODEL = "claude-opus-5-5"
# The model's stated knowledge cutoff is mid-2026; markets must close and resolve after this.
TRAINING_CUTOFF_TS = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp())
PROMPT_VERSION = "v1"
SYSTEM = (
    "You are a careful forecaster. Estimate the probability that a prediction-market question "
    "resolves YES. You have no access to news or prices; reason from base rates and what you know. "
    "Be calibrated: when you're unsure, stay closer to the base rate for questions of this kind."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "probability": {"type": "number", "description": "P(YES), between 0 and 1"},
        "rationale": {"type": "string", "description": "One or two sentences"},
    },
    "required": ["probability", "rationale"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class LLMForecast:
    source: str
    market_id: str
    model: str
    prompt_version: str
    p: float
    rationale: str


def build_prompt(title: str, scheduled_close_ts: int) -> str:
    """Only the question and the date. No price, no volume, no outcome, no category hints."""
    close = datetime.fromtimestamp(scheduled_close_ts, UTC).strftime("%Y-%m-%d")
    return (
        f"Question: {title}\n"
        f"Scheduled to be decided by: {close}\n\n"
        "What is the probability this resolves YES?"
    )


def eligible(close_ts: int, resolved_ts: int | None, cutoff: int = TRAINING_CUTOFF_TS) -> bool:
    return close_ts > cutoff and resolved_ts is not None and resolved_ts > cutoff


def cache_path(source: str, market_id: str, raw_dir: Path = RAW_DIR) -> Path:
    key = hashlib.sha1(f"{source}:{market_id}".encode()).hexdigest()[:16]
    return raw_dir / "llm" / MODEL / PROMPT_VERSION / f"{key}.json"


def parse_response(payload: dict[str, Any]) -> tuple[float, str]:
    data = json.loads(payload["text"])
    p = float(data["probability"])
    if p > 1.0 and p <= 100.0:  # tolerate a percentage
        p /= 100.0
    return min(max(p, 0.0), 1.0), str(data.get("rationale", ""))


def forecast_one(
    messages: Any,  # client.beta.messages, or a test fake with .create()
    source: str,
    market_id: str,
    title: str,
    scheduled_close_ts: int,
    raw_dir: Path = RAW_DIR,
) -> LLMForecast | None:
    path = cache_path(source, market_id, raw_dir)
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        prompt = build_prompt(title, scheduled_close_ts)
        resp = messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            payload = {"refused": True, "prompt": prompt, "model": resp.model}
        else:
            text = next(b.text for b in resp.content if b.type == "text")
            payload = {
                "text": text,
                "prompt": prompt,
                "model": resp.model,
                "stop_reason": resp.stop_reason,
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    if payload.get("refused"):
        return None
    p, rationale = parse_response(payload)
    return LLMForecast(source, market_id, MODEL, PROMPT_VERSION, p, rationale)


def run(limit: int | None) -> None:
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()
    client = anthropic.Anthropic()
    con = db.connect()
    markets = con.execute("""
        SELECT DISTINCT m.source, m.market_id, m.title, m.scheduled_close_ts, m.close_ts,
               m.resolved_ts
        FROM markets m JOIN snapshots s USING (source, market_id)
        ORDER BY m.source, m.market_id
    """).df()
    rows = [
        r
        for r in markets.to_dict("records")
        if eligible(
            int(r["close_ts"]),
            None if r["resolved_ts"] != r["resolved_ts"] else int(r["resolved_ts"]),
        )
    ]
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} eligible markets (closed and resolved after the training cutoff)")
    out: list[LLMForecast] = []
    for i, r in enumerate(rows, 1):
        f = forecast_one(
            client.beta.messages,
            str(r["source"]),
            str(r["market_id"]),
            str(r["title"]),
            int(r["scheduled_close_ts"]),
        )
        if f:
            out.append(f)
        if i % 25 == 0:
            print(f"  {i}/{len(rows)}")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_forecasts (
        source VARCHAR, market_id VARCHAR, model VARCHAR, prompt_version VARCHAR, p DOUBLE,
        rationale VARCHAR)""")
    con.execute(
        "DELETE FROM llm_forecasts WHERE model = ? AND prompt_version = ?", [MODEL, PROMPT_VERSION]
    )
    if out:
        df = db.to_frame(out)  # noqa: F841
        con.execute("INSERT INTO llm_forecasts SELECT * FROM df")
    con.close()
    print(f"stored {len(out)} forecasts ({len(rows) - len(out)} refused)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    run(parser.parse_args().limit)


if __name__ == "__main__":
    main()
