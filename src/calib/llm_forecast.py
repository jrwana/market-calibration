"""LLM forecaster baseline: the model sees only the question and the scheduled close date.

Leakage rule: a market is eligible only if it closed AND resolved after *that model's* training
cutoff; otherwise the model may simply remember the answer. Each backend carries its own cutoff.

Backends:
  local   self-hosted vLLM (gpt-oss-20b), OpenAI-compatible HTTP. Free, temperature 0. Its cutoff
          (June 2024) predates the whole study window, so every sampled market is eligible.
  claude  hosted Claude via the official SDK. Current models reject sampling parameters, so
          determinism comes from the frozen prompt and the cache. Only markets resolved after
          mid-2026 are eligible.

Every response is cached under ``data/raw/llm/<model>/<prompt_version>/`` and never re-requested.

Run: ``python -m calib.llm_forecast --backend local [--limit N]``
     ``python -m calib.llm_forecast --backend claude [--limit N]``  (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from calib import db
from calib.config import RAW_DIR


def _ts(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


PROMPT_VERSION = "v2"  # v2: 8k token ceiling; truncated answers recorded as failures
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


def eligible(close_ts: int, resolved_ts: int | None, cutoff: int) -> bool:
    return close_ts > cutoff and resolved_ts is not None and resolved_ts > cutoff


def parse_response(payload: dict[str, Any]) -> tuple[float, str]:
    data = json.loads(payload["text"])
    p = float(data["probability"])
    if 1.0 < p <= 100.0:  # tolerate a percentage
        p /= 100.0
    return min(max(p, 0.0), 1.0), str(data.get("rationale", ""))


# ---------------------------------------------------------------- backends


class Backend:
    """A model plus how to ask it. ``ask`` returns a cacheable payload dict."""

    model: str
    cutoff_ts: int

    def ask(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError


class LocalBackend(Backend):
    """Self-hosted vLLM, OpenAI-compatible chat completions over plain HTTP."""

    cutoff_ts = _ts(2024, 7, 1)  # gpt-oss model card: knowledge cutoff June 2024

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        http: Any = None,
        effort: str = "medium",
    ) -> None:
        self.endpoint = (
            endpoint or os.getenv("LOCAL_LLM_ENDPOINT") or "http://192.168.8.187:8000/v1"
        ).rstrip("/")
        self.model_id = model or os.getenv("LOCAL_LLM_MODEL") or "openai/gpt-oss-20b"
        self.effort = effort
        # The stored/cached name carries the effort: a different setting is a different forecaster.
        self.model = f"{self.model_id}@{effort}"
        self.http = http or httpx.Client(timeout=300.0)

    def ask(self, prompt: str) -> dict[str, Any]:
        resp = self.http.post(
            f"{self.endpoint}/chat/completions",
            json={
                "model": self.model_id,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 8000,
                "reasoning_effort": self.effort,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "forecast", "schema": SCHEMA, "strict": True},
                },
            },
        )
        resp.raise_for_status()
        d = resp.json()
        choice = d["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if not text:  # a reasoning model that hit the token ceiling mid-thought
            return {"failed": "empty_content", "finish_reason": choice.get("finish_reason")}
        if choice.get("finish_reason") == "length":  # JSON cut off mid-answer
            return {"failed": "truncated", "text": text, "finish_reason": "length"}
        return {
            "text": text,
            "model": d.get("model", self.model_id),
            "finish_reason": choice.get("finish_reason"),
        }


class ClaudeBackend(Backend):
    """Hosted Claude through the official SDK, with server-side refusal fallback enabled."""

    model = "claude-opus-5-5"
    cutoff_ts = _ts(2026, 7, 1)  # stated knowledge cutoff is mid-2026

    def __init__(self, messages: Any = None) -> None:
        if messages is None:
            import anthropic
            from dotenv import load_dotenv

            load_dotenv()
            messages = anthropic.Anthropic().beta.messages
        self.messages = messages

    def ask(self, prompt: str) -> dict[str, Any]:
        resp = self.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return {"failed": "refusal", "model": resp.model}
        text = next(b.text for b in resp.content if b.type == "text")
        return {"text": text, "model": resp.model, "stop_reason": resp.stop_reason}


# ---------------------------------------------------------------- caching + pipeline


def cache_path(model: str, source: str, market_id: str, raw_dir: Path = RAW_DIR) -> Path:
    key = hashlib.sha1(f"{source}:{market_id}".encode()).hexdigest()[:16]
    return raw_dir / "llm" / model.replace("/", "__") / PROMPT_VERSION / f"{key}.json"


def forecast_one(
    backend: Backend,
    source: str,
    market_id: str,
    title: str,
    scheduled_close_ts: int,
    raw_dir: Path = RAW_DIR,
) -> LLMForecast | None:
    path = cache_path(backend.model, source, market_id, raw_dir)
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        prompt = build_prompt(title, scheduled_close_ts)
        payload = {**backend.ask(prompt), "prompt": prompt}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    if payload.get("failed"):
        return None
    try:
        p, rationale = parse_response(payload)
    except (ValueError, KeyError):
        return None
    return LLMForecast(source, market_id, backend.model, PROMPT_VERSION, p, rationale)


def make_backend(name: str, effort: str = "medium") -> Backend:
    if name == "local":
        return LocalBackend(effort=effort)
    if name == "claude":
        return ClaudeBackend()
    raise ValueError(f"unknown backend {name!r} (use 'local' or 'claude')")


def run(backend: Backend, limit: int | None, workers: int = 1) -> None:
    con = db.connect()
    markets = con.execute("""
        SELECT DISTINCT m.source, m.market_id, m.title, m.scheduled_close_ts, m.close_ts,
               m.resolved_ts
        FROM markets m JOIN snapshots s USING (source, market_id)
        ORDER BY m.source, m.market_id
    """).df()
    con.close()  # don't hold the DuckDB write lock during a long run
    rows = [
        r
        for r in markets.to_dict("records")
        if eligible(
            int(r["close_ts"]),
            None if r["resolved_ts"] != r["resolved_ts"] else int(r["resolved_ts"]),
            backend.cutoff_ts,
        )
    ]
    if limit:
        rows = rows[:limit]
    cutoff = datetime.fromtimestamp(backend.cutoff_ts, UTC).date()
    print(f"{backend.model}: {len(rows)} eligible markets (closed and resolved after {cutoff})")
    out: list[LLMForecast] = []

    def one(r: dict[str, Any]) -> LLMForecast | None:
        return forecast_one(
            backend,
            str(r["source"]),
            str(r["market_id"]),
            str(r["title"]),
            int(r["scheduled_close_ts"]),
        )

    # vLLM batches concurrent requests, so a small pool cuts wall-clock time several-fold.
    # Results are keyed by market, so completion order doesn't matter.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, f in enumerate(pool.map(one, rows), 1):
            if f:
                out.append(f)
            if i % 100 == 0:
                print(f"  {i}/{len(rows)}", flush=True)
    con = db.connect()
    con.execute("""CREATE TABLE IF NOT EXISTS llm_forecasts (
        source VARCHAR, market_id VARCHAR, model VARCHAR, prompt_version VARCHAR, p DOUBLE,
        rationale VARCHAR)""")
    con.execute(
        "DELETE FROM llm_forecasts WHERE model = ? AND prompt_version = ?",
        [backend.model, PROMPT_VERSION],
    )
    if out:
        df = db.to_frame(out)  # noqa: F841
        con.execute("INSERT INTO llm_forecasts SELECT * FROM df")
    con.close()
    print(f"stored {len(out)} forecasts ({len(rows) - len(out)} failed or refused)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="local", choices=["local", "claude"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--effort",
        default="medium",
        choices=["low", "medium", "high"],
        help="local backend reasoning effort",
    )
    parser.add_argument("--workers", type=int, default=None, help="default: 8 local, 2 claude")
    args = parser.parse_args()
    workers = args.workers or (8 if args.backend == "local" else 2)
    run(make_backend(args.backend, args.effort), args.limit, workers)


if __name__ == "__main__":
    main()
