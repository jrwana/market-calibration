"""LLM baseline: leakage filter, prompt contents, caching, both backends. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from calib import llm_forecast as L

ANSWER = '{"probability": 0.3, "rationale": "base rate"}'


class FakeMessages:
    """Stands in for client.beta.messages."""

    def __init__(self, text: str = ANSWER, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self.text, self.stop_reason = text, stop_reason

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=self.text)
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason, model=kwargs["model"])


class FakeHTTP:
    """Stands in for httpx.Client against a vLLM OpenAI-compatible server."""

    def __init__(self, content: str | None = ANSWER, finish: str = "stop") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content, self.finish = content, finish

    def post(self, url: str, json: dict[str, Any]) -> Any:
        self.calls.append({"url": url, **json})
        body = {
            "model": json["model"],
            "choices": [{"message": {"content": self.content}, "finish_reason": self.finish}],
        }
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


# ---------------------------------------------------------------- leakage


@pytest.mark.parametrize(
    ("close_off", "resolved_off", "ok"),
    [(10, 20, True), (-10, 20, False), (10, -5, False), (10, None, False)],
)
def test_only_post_cutoff_markets(close_off: int, resolved_off: int | None, ok: bool) -> None:
    cut = L.ClaudeBackend.cutoff_ts
    resolved = None if resolved_off is None else cut + resolved_off
    assert L.eligible(cut + close_off, resolved, cut) is ok


def test_each_backend_has_its_own_cutoff() -> None:
    assert L.LocalBackend.cutoff_ts < L.ClaudeBackend.cutoff_ts
    # gpt-oss's cutoff predates the study window, so the whole sample is eligible for it.
    from calib.config import WINDOW_START_TS

    assert L.LocalBackend.cutoff_ts < WINDOW_START_TS


def test_prompt_has_only_question_and_date() -> None:
    prompt = L.build_prompt("Will it rain in Seattle on Dec 1?", L._ts(2026, 7, 31))
    assert "Will it rain in Seattle on Dec 1?" in prompt
    assert "2026-07-31" in prompt
    for leak in ("price", "volume", "resolved", "outcome", "%"):
        assert leak not in prompt.lower()


# ---------------------------------------------------------------- caching


@pytest.mark.parametrize("which", ["local", "claude"])
def test_responses_are_cached(tmp_path: Path, which: str) -> None:
    fake: Any
    backend: L.Backend
    if which == "local":
        fake = FakeHTTP()
        backend = L.LocalBackend(endpoint="http://x/v1", model="openai/gpt-oss-20b", http=fake)
    else:
        fake = FakeMessages()
        backend = L.ClaudeBackend(messages=fake)
    a = L.forecast_one(backend, "kalshi", "M1", "Q?", 10**9, raw_dir=tmp_path)
    b = L.forecast_one(backend, "kalshi", "M1", "Q?", 10**9, raw_dir=tmp_path)
    assert a == b and a is not None and a.p == pytest.approx(0.3)
    assert len(fake.calls) == 1
    stored = json.loads(L.cache_path(backend.model, "kalshi", "M1", tmp_path).read_text())
    assert "prompt" in stored and "text" in stored


def test_cache_is_per_model(tmp_path: Path) -> None:
    assert L.cache_path("openai/gpt-oss-20b", "s", "m", tmp_path) != L.cache_path(
        "claude-opus-5-5", "s", "m", tmp_path
    )


# ---------------------------------------------------------------- request shapes


def test_local_request_is_deterministic_and_structured(tmp_path: Path) -> None:
    http = FakeHTTP()
    L.forecast_one(
        L.LocalBackend("http://x/v1", "openai/gpt-oss-20b", http),
        "k",
        "M",
        "Q?",
        10**9,
        raw_dir=tmp_path,
    )
    call = http.calls[0]
    assert call["url"] == "http://x/v1/chat/completions"
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"


def test_claude_request_has_no_sampling_params(tmp_path: Path) -> None:
    fake = FakeMessages()
    L.forecast_one(L.ClaudeBackend(messages=fake), "k", "M", "Q?", 10**9, raw_dir=tmp_path)
    call = fake.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call  # rejected by current Claude models


def test_failures_are_cached_and_skipped(tmp_path: Path) -> None:
    empty = L.LocalBackend("http://x/v1", "m", FakeHTTP(content=None))
    refused = L.ClaudeBackend(messages=FakeMessages(stop_reason="refusal"))
    assert L.forecast_one(empty, "k", "A", "Q?", 10**9, raw_dir=tmp_path) is None
    assert L.forecast_one(refused, "k", "B", "Q?", 10**9, raw_dir=tmp_path) is None
    assert L.cache_path(empty.model, "k", "A", tmp_path).exists()


@pytest.mark.parametrize(("raw", "p"), [(0.42, 0.42), (42, 0.42), (-0.1, 0.0), (1.5, 0.015)])
def test_parse_clips_and_tolerates_percentages(raw: float, p: float) -> None:
    got, _ = L.parse_response({"text": json.dumps({"probability": raw, "rationale": ""})})
    assert got == pytest.approx(p)


def test_truncated_local_answer_is_a_failure(tmp_path: Path) -> None:
    cut = L.LocalBackend(
        "http://x/v1", "m", FakeHTTP(content='{"probability": 0.', finish="length")
    )
    assert L.forecast_one(cut, "k", "T", "Q?", 10**9, raw_dir=tmp_path) is None
    stored = json.loads(L.cache_path(cut.model, "k", "T", tmp_path).read_text())
    assert stored["failed"] == "truncated"


def test_effort_is_part_of_the_forecaster_identity(tmp_path: Path) -> None:
    http = FakeHTTP()
    low = L.LocalBackend("http://x/v1", "openai/gpt-oss-20b", http, effort="low")
    L.forecast_one(low, "k", "E", "Q?", 10**9, raw_dir=tmp_path)
    assert http.calls[0]["reasoning_effort"] == "low"
    assert http.calls[0]["model"] == "openai/gpt-oss-20b"  # the server sees the real model id
    assert low.model == "openai/gpt-oss-20b@low"  # cache and results keep runs apart
