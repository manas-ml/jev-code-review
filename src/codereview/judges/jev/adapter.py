"""The ONLY module that knows Jev's wire format.

Verified against OpenRouter docs (openrouter.ai/docs/guides/community/jev-tutorial and the
how-to-use-jev tutorial): POST /api/alpha/decisions with {model, state, questions}; answers
keyed by question id with type-specific fields:
  noul:   {"type":"noul","noul":p}                       (no confidence field)
  choice: {"type":"choice","choice":k,"confidence":c,"probabilities":{...}}
  score:  {"type":"score","score":x,"confidence":c,"probabilities":{...},"legend":{...}}
                                                         (x is zero-based, continuous)
If the API changes, edit here.
"""

from __future__ import annotations

from typing import Any

from codereview.judges.base import JudgeError
from codereview.judges.jev.questions import GATE_QUESTIONS, TRIAGE_QUESTIONS, Question
from codereview.models import GateResult, Scored, TriageResult

DECISIONS_PATH = "/api/alpha/decisions"


def build_question(q: Question) -> dict[str, Any]:
    body: dict[str, Any] = {"type": q.kind, "instructions": q.instructions}
    if q.kind == "choice":
        body["criteria"] = {o: o.replace("_", " ") for o in q.options}
    elif q.kind == "score":
        body["criteria"] = list(q.options)
    return body


def build_request(
    model: str, state: dict[str, Any], questions: tuple[Question, ...]
) -> dict[str, Any]:
    return {
        "model": model,
        "state": state,
        "questions": {q.id: build_question(q) for q in questions},
    }


def _answer(answers: dict[str, Any], qid: str, kind: str) -> dict[str, Any]:
    a = answers.get(qid)
    if not isinstance(a, dict) or a.get("type", kind) != kind:
        raise JudgeError(f"missing or malformed answer for {qid!r}")
    return a


def _noul(answers: dict[str, Any], qid: str) -> Scored:
    a = _answer(answers, qid, "noul")
    try:
        return Scored(value=float(a["noul"]), confidence=float(a.get("confidence", 1.0)))
    except (KeyError, TypeError, ValueError) as e:
        raise JudgeError(f"bad noul answer for {qid!r}") from e


def _score(answers: dict[str, Any], qid: str) -> Scored:
    a = _answer(answers, qid, "score")
    try:
        return Scored(value=float(a["score"]), confidence=float(a.get("confidence", 1.0)))
    except (KeyError, TypeError, ValueError) as e:
        raise JudgeError(f"bad score answer for {qid!r}") from e


def parse_triage(answers: dict[str, Any]) -> TriageResult:
    ct = _answer(answers, "change_type", "choice")
    try:
        return TriageResult(
            change_type=ct["choice"],
            change_type_confidence=float(ct.get("confidence", 1.0)),
            risk=_score(answers, "risk"),
            trivial=_noul(answers, "trivial"),
            touches_sensitive=_noul(answers, "touches_sensitive"),
            changes_public_contract=_noul(answers, "changes_public_contract"),
            lacks_test_coverage=_noul(answers, "lacks_test_coverage"),
            perf_sensitive=_noul(answers, "perf_sensitive"),
        )
    except (KeyError, ValueError) as e:
        raise JudgeError(f"bad triage answers: {e}") from e


def parse_gate(answers: dict[str, Any]) -> GateResult:
    return GateResult(
        is_real_defect=_noul(answers, "is_real_defect"),
        severity=_score(answers, "severity"),
        actionable=_noul(answers, "actionable"),
        duplicate=_noul(answers, "duplicate"),
    )


def usage(resp: dict[str, Any]) -> tuple[int, int, float]:
    u = resp.get("usage") or {}
    return int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)), float(u.get("cost", 0.0))


__all__ = [
    "DECISIONS_PATH",
    "GATE_QUESTIONS",
    "TRIAGE_QUESTIONS",
    "build_request",
    "parse_gate",
    "parse_triage",
    "usage",
]
