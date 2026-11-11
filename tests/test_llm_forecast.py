"""LLM baseline: leakage filter, prompt contents, caching. Uses a fake client; no network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from calib import llm_forecast as L

CUT = L.TRAINING_CUTOFF_TS


class FakeMessages:
    def __init__(self, text: str = '{"probability": 0.3, "rationale": "base rate"}') -> None:
        self.calls: list[dict[str, Any]] = []
        self.text = text

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=self.text)
        return SimpleNamespace(content=[block], stop_reason="end_turn", model=kwargs["model"])


@pytest.mark.parametrize(
    ("close", "resolved", "ok"),
    [
        (CUT + 10, CUT + 20, True),
        (CUT - 10, CUT + 20, False),  # trading ended before the cutoff
        (CUT + 10, CUT - 5, False),
        (CUT + 10, None, False),
    ],
)
def test_only_post_cutoff_markets(close: int, resolved: int | None, ok: bool) -> None:
    assert L.eligible(close, resolved) is ok


def test_prompt_has_only_question_and_date() -> None:
    prompt = L.build_prompt("Will it rain in Seattle on Dec 1?", CUT + 86400 * 30)
    assert "Will it rain in Seattle on Dec 1?" in prompt
    assert "2026-07-31" in prompt
    for leak in ("price", "volume", "resolved", "outcome", "%"):
        assert leak not in prompt.lower()


def test_responses_are_cached(tmp_path: Path) -> None:
    fake = FakeMessages()
    a = L.forecast_one(fake, "kalshi", "M1", "Q?", CUT + 100, raw_dir=tmp_path)
    b = L.forecast_one(fake, "kalshi", "M1", "Q?", CUT + 100, raw_dir=tmp_path)
    assert a == b
    assert len(fake.calls) == 1
    assert a is not None and a.p == pytest.approx(0.3)
    stored = json.loads(L.cache_path("kalshi", "M1", tmp_path).read_text())
    assert "prompt" in stored and "text" in stored


def test_request_uses_structured_output_and_no_sampling_params(tmp_path: Path) -> None:
    fake = FakeMessages()
    L.forecast_one(fake, "kalshi", "M2", "Q?", CUT + 100, raw_dir=tmp_path)
    call = fake.calls[0]
    assert call["model"] == L.MODEL
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call  # rejected by current models; caching gives determinism


@pytest.mark.parametrize(("raw", "p"), [(0.42, 0.42), (42, 0.42), (-0.1, 0.0), (1.5, 0.015)])
def test_parse_clips_and_tolerates_percentages(raw: float, p: float) -> None:
    got, _ = L.parse_response({"text": json.dumps({"probability": raw, "rationale": ""})})
    assert got == pytest.approx(p)
