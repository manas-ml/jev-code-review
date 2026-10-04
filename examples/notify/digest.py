"""Daily digest: group a user's pending notifications by category."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any


@dataclass(frozen=True)
class Note:
    user_id: int
    category: str
    text: str


def load_templates(path: str) -> dict[str, Any]:
    import yaml  # optional dependency, only needed for templates

    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return data


def build_digest(notes: list[Note]) -> dict[str, list[str]]:
    digest: dict[str, list[str]] = {}
    for category, group in groupby(notes, key=lambda n: n.category):
        digest[category] = [n.text for n in group]
    return digest
