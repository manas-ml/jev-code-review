"""Render judge states as delimited text (shared by JevJudge and LLMJudge)."""

from __future__ import annotations

from codereview.context.builder import UNTRUSTED_NOTICE, delimit
from codereview.models import GateState


def render_gate_text(state: GateState) -> str:
    f = state.finding
    finding = (
        f"file: {f.file}\nline: {f.line}\ntitle: {f.title}\nexplanation: {f.explanation}\n"
        f"evidence: {f.evidence}\nsuggested_fix: {f.suggested_fix or ''}"
    )
    existing = "\n".join(
        f"{c.file}:{c.line}: {c.body}" for c in state.existing_comments if c.file == f.file
    )
    return "\n\n".join(
        [
            UNTRUSTED_NOTICE,
            delimit("PR_TITLE", state.pr_title),
            delimit("FINDING", finding),
            delimit("CODE", state.code_window),
            delimit("EXISTING_COMMENTS", existing),
        ]
    )
