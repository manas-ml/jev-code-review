"""Cluster duplicate findings BEFORE the gate (no embeddings).

Findings are linked when they are in the same file, within `window` lines, and share title
tokens. Each cluster is gated once, through one representative, so the result does not depend
on agent order and duplicates cost no judge calls.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from codereview.models import Finding

_STOP = {"the", "a", "an", "of", "in", "to", "is", "and", "or", "for", "on", "with", "when"}

# Tie-break only: prefer findings from agents whose claims are about behavior.
AGENT_PRIORITY = {"security": 0, "correctness": 1, "performance": 2, "architecture": 3, "tests": 4}


def tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", title.lower()) if t not in _STOP}


def similar(a: Finding, b: Finding, window: int, min_overlap: float = 0.3) -> bool:
    if a.file != b.file or abs(a.line - b.line) > window:
        return False
    ta, tb = tokens(a.title), tokens(b.title)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= min_overlap


def cluster(findings: list[Finding], window: int) -> list[list[Finding]]:
    """Single-linkage clusters (union-find), returned in first-seen order."""
    parent = list(range(len(findings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            if similar(findings[i], findings[j], window):
                parent[find(j)] = find(i)
    groups: dict[int, list[Finding]] = {}
    for i, f in enumerate(findings):
        groups.setdefault(find(i), []).append(f)
    return list(groups.values())


def pick_representative(group: list[Finding], code_window: Callable[[Finding], str]) -> Finding:
    """Best-grounded member: evidence present in the code, then longer evidence, then agent
    priority, then id (deterministic)."""

    def key(f: Finding) -> tuple[int, int, int, str]:
        ev = f.evidence.strip()
        grounded = bool(ev) and ev in code_window(f)
        return (0 if grounded else 1, -len(ev), AGENT_PRIORITY.get(f.agent, 9), f.id)

    return min(group, key=key)
