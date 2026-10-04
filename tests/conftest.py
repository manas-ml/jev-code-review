from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from codereview.config import Settings
from codereview.context.builder import load_local
from codereview.llm.client import LLMResponse
from codereview.models import ReviewContext
from codereview.tracing import CallRecord, Tracer

FIX = Path(__file__).parent / "fixtures"

Handler = Callable[[str, list[dict[str, Any]]], str]


class FakeLLM:
    """Deterministic LLM: `handler(stage, messages)` returns the raw content string."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        stage: str,
        tracer: Tracer | None = None,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append((stage, messages))
        if tracer is not None:
            tracer.record(
                CallRecord(
                    kind="llm",
                    stage=stage,
                    model=model,
                    latency_s=0.0,
                    input_tokens=10,
                    output_tokens=5,
                )
            )
        return LLMResponse(content=self.handler(stage, messages), input_tokens=10, output_tokens=5)


def finding_json(
    file: str = "app/pricing.py", line: int = 22, title: str = "Division by zero"
) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "file": file,
                    "line": line,
                    "title": title,
                    "explanation": "len(items) is always 0 so this raises ZeroDivisionError",
                    "evidence": "return total * rate / len(items)",
                    "category": "bug",
                }
            ]
        }
    )


def default_handler(stage: str, messages: list[dict[str, Any]]) -> str:
    if stage == "agent:correctness":
        return finding_json()
    if stage.startswith("agent:"):
        return '{"findings": []}'
    if stage == "verifier":
        return '{"verdict": "confirmed", "reasoning": "traced", "evidence": "x"}'
    raise AssertionError(f"unexpected stage {stage}")


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, jev_mode="mock", openrouter_api_key="test")  # type: ignore[call-arg]


def load(name: str) -> ReviewContext:
    return load_local(FIX / name / "diff.patch", FIX / name / "meta.json")
