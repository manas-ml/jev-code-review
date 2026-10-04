"""JevJudge: Jev via OpenRouter's decisions endpoint."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from codereview.config import Settings
from codereview.judges.base import JudgeError
from codereview.judges.jev import adapter
from codereview.judges.jev.questions import GATE_QUESTIONS, TRIAGE_QUESTIONS, Question
from codereview.judges.render import render_gate_text
from codereview.models import GateResult, GateState, TriageResult, TriageState
from codereview.tracing import CallRecord, Tracer, hash_state

RETRY_STATUS = {429, 500, 502, 503, 504}


class JevJudge:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        *,
        max_retries: int = 4,
        backoff_base: float = 0.5,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=60)
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    async def _call(
        self,
        state: dict[str, Any],
        questions: tuple[Question, ...],
        stage: str,
        tracer: Tracer | None,
    ) -> dict[str, Any]:
        body = adapter.build_request(self.settings.jev_model, state, questions)
        url = self.settings.openrouter_base_url.removesuffix("/api/v1") + adapter.DECISIONS_PATH
        headers = {"Authorization": f"Bearer {self.settings.openrouter_api_key}"}
        start = time.perf_counter()
        last = ""
        data: dict[str, Any] | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(url, json=body, headers=headers)
            except httpx.TransportError as e:
                last = repr(e)
            else:
                if r.status_code in RETRY_STATUS:
                    last = f"HTTP {r.status_code}"
                elif r.status_code >= 400:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    break
                else:
                    data = r.json()
                    break
            if attempt < self.max_retries:
                await asyncio.sleep(self.backoff_base * 2**attempt)
        latency = time.perf_counter() - start
        h = hash_state(json.dumps(state, sort_keys=True, default=str))
        if data is None:
            self._record(tracer, stage, latency, h, None, last)
            raise JudgeError(f"Jev call failed: {last}")
        self._record(tracer, stage, latency, h, data, None)
        return data

    def _record(
        self,
        tracer: Tracer | None,
        stage: str,
        latency: float,
        h: str,
        data: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        if tracer is None:
            return
        inp, out, cost = adapter.usage(data) if data else (0, 0, 0.0)
        tracer.record(
            CallRecord(
                kind="jev",
                stage=stage,
                model=self.settings.jev_model,
                latency_s=latency,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
                request_hash=h,
                answers=data.get("answers") if data else None,
                error=error,
            )
        )

    async def triage(self, state: TriageState, tracer: Tracer | None = None) -> TriageResult:
        data = await self._call({"pull_request": state.text}, TRIAGE_QUESTIONS, "triage", tracer)
        return adapter.parse_triage(data.get("answers") or {})

    async def gate(self, state: GateState, tracer: Tracer | None = None) -> GateResult:
        data = await self._call(
            {"finding": render_gate_text(state)}, GATE_QUESTIONS, "gate", tracer
        )
        return adapter.parse_gate(data.get("answers") or {})
