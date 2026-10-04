"""Per-run tracing: stage latency, Jev/LLM calls, tokens, cost. Secrets are redacted."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\"',]{8,}"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()[:16]


class CallRecord(BaseModel):
    kind: str  # "llm" | "jev"
    stage: str
    model: str
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    request_hash: str = ""
    answers: dict[str, Any] | None = None
    error: str | None = None


class Tracer:
    def __init__(self, pipeline: str, pr_id: str) -> None:
        self.pipeline = pipeline
        self.pr_id = pr_id
        self.stages: dict[str, float] = defaultdict(float)
        self.calls: list[CallRecord] = []
        self.extra: dict[str, Any] = {}
        self._current_stage = "unknown"

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[None]:
        prev, self._current_stage = self._current_stage, name
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] += time.perf_counter() - start
            self._current_stage = prev

    @property
    def current_stage(self) -> str:
        return self._current_stage

    def record(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def cost_by_stage(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for c in self.calls:
            out[c.stage] += c.cost_usd
        return dict(out)

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_latency(self) -> float:
        return sum(self.stages.values())

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = redact_obj(
            {
                "pipeline": self.pipeline,
                "pr_id": self.pr_id,
                "stage_latency_s": dict(self.stages),
                "total_latency_s": self.total_latency,
                "cost_by_stage_usd": self.cost_by_stage(),
                "total_cost_usd": self.total_cost,
                "calls": [c.model_dump() for c in self.calls],
                **self.extra,
            }
        )
        return out

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
