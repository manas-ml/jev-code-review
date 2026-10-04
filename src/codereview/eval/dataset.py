"""Labeled PR dataset: eval/dataset/<pr-id>/{meta.json, diff.patch, labels.json}."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from codereview.context.builder import load_local
from codereview.models import ReviewContext


class LabeledIssue(BaseModel):
    file: str
    line_start: int
    line_end: int | None = None
    description: str = ""

    @property
    def end(self) -> int:
        return self.line_end if self.line_end is not None else self.line_start


class Nitpick(BaseModel):
    file: str
    line: int


class Labels(BaseModel):
    issues: list[LabeledIssue] = Field(default_factory=list)
    nitpicks: list[Nitpick] = Field(default_factory=list)


class Example(BaseModel):
    pr_id: str
    context: ReviewContext
    labels: Labels

    model_config = {"arbitrary_types_allowed": True}


def load_dataset(root: Path) -> list[Example]:
    out: list[Example] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "diff.patch").exists():
            continue
        ctx = load_local(d / "diff.patch", d / "meta.json")
        ctx.pr_id = d.name
        labels_path = d / "labels.json"
        labels = (
            Labels.model_validate(json.loads(labels_path.read_text()))
            if labels_path.exists()
            else Labels()
        )
        out.append(Example(pr_id=d.name, context=ctx, labels=labels))
    return out
