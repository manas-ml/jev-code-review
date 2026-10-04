"""Rank by effective severity, then gate probability; cap inline comments."""

from __future__ import annotations

from codereview.models import FindingRecord


def rank_key(r: FindingRecord) -> tuple[float, float, str]:
    return (-(r.severity or 0.0), -(r.gate_score or 0.0), r.finding.id)


def rank_and_cap(
    records: list[FindingRecord], cap: int
) -> tuple[list[FindingRecord], list[FindingRecord]]:
    ordered = [
        r.model_copy(update={"rank": i}) for i, r in enumerate(sorted(records, key=rank_key), 1)
    ]
    inline = ordered[:cap]
    overflow = [
        r.model_copy(update={"disposition": "overflow", "reason": f"beyond top {cap}"})
        for r in ordered[cap:]
    ]
    return inline, overflow
