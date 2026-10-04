"""Judge protocol: fast, constrained decisions (routing and filtering)."""

from __future__ import annotations

from typing import Protocol

from codereview.models import GateResult, GateState, TriageResult, TriageState
from codereview.tracing import Tracer


class JudgeError(RuntimeError):
    """Raised when a judge call fails; callers must fail safe toward more review."""


class Judge(Protocol):
    async def triage(self, state: TriageState, tracer: Tracer | None = None) -> TriageResult: ...

    async def gate(self, state: GateState, tracer: Tracer | None = None) -> GateResult: ...
