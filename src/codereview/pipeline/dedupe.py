"""Cheap dedupe: same file, nearby line, overlapping title tokens (no embeddings)."""

from __future__ import annotations

import re

from codereview.models import FindingRecord

_STOP = {"the", "a", "an", "of", "in", "to", "is", "and", "or", "for", "on", "with", "when"}


def tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", title.lower()) if t not in _STOP}


def similar(a: FindingRecord, b: FindingRecord, window: int, min_overlap: float = 0.3) -> bool:
    fa, fb = a.finding, b.finding
    if fa.file != fb.file or abs(fa.line - fb.line) > window:
        return False
    ta, tb = tokens(fa.title), tokens(fb.title)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= min_overlap


def dedupe(
    records: list[FindingRecord], window: int
) -> tuple[list[FindingRecord], list[FindingRecord]]:
    """Returns (kept, dropped). Within a cluster keep the highest-ranked record."""
    from codereview.pipeline.rank import rank_key

    kept: list[FindingRecord] = []
    dropped: list[FindingRecord] = []
    for rec in sorted(records, key=rank_key):
        dup_of = next((k for k in kept if similar(rec, k, window)), None)
        if dup_of is None:
            kept.append(rec)
        else:
            dropped.append(
                rec.model_copy(
                    update={
                        "disposition": "deduped",
                        "reason": f"duplicate of {dup_of.finding.id}",
                    }
                )
            )
    return kept, dropped
