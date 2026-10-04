"""Deterministic heuristic judge for offline dev and CI. NOT a quality signal.

Uses only structural signals (paths, churn, a few code tokens), never PR prose, so it
cannot be steered by embedded instructions.
"""

from __future__ import annotations

import re

from codereview.models import (
    ChangeType,
    GateResult,
    GateState,
    Scored,
    TriageResult,
    TriageState,
)
from codereview.tracing import Tracer

_SENSITIVE = re.compile(r"(auth|secret|crypt|token|password|session|permission|migrat)", re.I)
_RISKY_CODE = re.compile(r"os\.system|eval\(|exec\(|subprocess|compare_digest|== token|/ len\(")


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _change_type(paths: list[str]) -> ChangeType:
    if all(p.endswith((".md", ".rst", ".txt")) for p in paths):
        return "docs_only"
    if all("test" in p.lower() for p in paths):
        return "tests_only"
    if all(p.endswith((".lock", "requirements.txt")) for p in paths):
        return "dependency_bump"
    if all(p.endswith((".yml", ".yaml", ".toml", ".json", ".cfg")) for p in paths):
        return "config_infra"
    return "bugfix"


class MockJevJudge:
    async def triage(self, state: TriageState, tracer: Tracer | None = None) -> TriageResult:
        ctx = state.context
        paths = [f.path for f in ctx.files]
        churn = ctx.churn
        added = "\n".join(
            ln for f in ctx.files for ln in f.patch.splitlines() if ln.startswith(("+", "-"))
        )
        change = _change_type(paths) if paths else "docs_only"
        sensitive = 0.9 if any(_SENSITIVE.search(p) for p in paths) else 0.05
        if _RISKY_CODE.search(added):
            sensitive = max(sensitive, 0.5)
        docs = change in ("docs_only", "tests_only")
        trivial = 0.95 if docs and churn <= 20 else _clamp(0.6 - churn / 200)
        risk = 0.0 if docs else min(4.0, 1.0 + churn / 100 + 2.0 * sensitive)
        touched_code = not docs
        has_tests = any("test" in p.lower() for p in paths)

        def s(v: float) -> Scored:
            return Scored(value=v, confidence=0.9)

        return TriageResult(
            change_type=change,
            change_type_confidence=0.9,
            risk=s(risk),
            trivial=s(trivial),
            touches_sensitive=s(sensitive),
            changes_public_contract=s(0.2 if touched_code else 0.02),
            lacks_test_coverage=s(0.7 if touched_code and not has_tests else 0.1),
            perf_sensitive=s(0.1),
        )

    async def gate(self, state: GateState, tracer: Tracer | None = None) -> GateResult:
        f = state.finding
        has_evidence = bool(f.evidence.strip()) and f.evidence.strip() in state.code_window
        real = 0.85 if has_evidence else 0.5
        dup = 0.0
        for c in state.existing_comments:
            if c.file == f.file and c.line is not None and abs(c.line - f.line) <= 3:
                dup = 0.9
        for o in state.other_findings:
            if o.id != f.id and o.file == f.file and abs(o.line - f.line) <= 3:
                dup = max(dup, 0.4)
        sev = 3.0 if _RISKY_CODE.search(f.evidence) else 1.5
        return GateResult(
            is_real_defect=Scored(value=real, confidence=0.9),
            severity=Scored(value=sev, confidence=0.9),
            actionable=Scored(value=0.8 if has_evidence else 0.4, confidence=0.9),
            duplicate=Scored(value=dup, confidence=0.9),
        )
