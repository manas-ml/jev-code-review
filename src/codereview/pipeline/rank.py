"""Rank by severity, then gate probability; cap inline comments."""

from __future__ import annotations

from codereview.models import FindingRecord


def rank_key(r: FindingRecord) -> tuple[float, float, str]:
    sev = r.gate.severity.value if r.gate else 0.0
    return (-sev, -(r.gate_score or 0.0), r.finding.id)


def rank_and_cap(
    records: list[FindingRecord], cap: int
) -> tuple[list[FindingRecord], list[FindingRecord]]:
    ordered = sorted(records, key=rank_key)
    inline = ordered[:cap]
    overflow = [
        r.model_copy(update={"disposition": "overflow", "reason": f"beyond top {cap}"})
        for r in ordered[cap:]
    ]
    return inline, overflow
