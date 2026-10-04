"""Scoring: matching rule, precision/recall, cost, latency, calibration.

Matching rule (documented in README): a finding matches a labeled issue if it is in the same
file and its line is within `match_line_window` (default 5) of the labeled range; otherwise,
if an LLM judge (prompt in eval/prompts/match_judge.md) says they describe the same issue.
Judge verdicts are cached on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from codereview.config import Settings
from codereview.eval.dataset import Example, LabeledIssue
from codereview.llm.client import LLM, LLMError, chat_validated
from codereview.models import Finding, ReviewResult

PROMPT = (Path(__file__).parent / "prompts" / "match_judge.md").read_text()


class _Same(BaseModel):
    same: bool
    reason: str = ""


class Matcher:
    def __init__(
        self, settings: Settings, llm: LLM | None = None, cache_path: Path | None = None
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.cache_path = cache_path
        self.cache: dict[str, bool] = {}
        if cache_path and cache_path.exists():
            self.cache = json.loads(cache_path.read_text())

    def line_match(self, f: Finding, issue: LabeledIssue) -> bool:
        w = self.settings.match_line_window
        return f.file == issue.file and issue.line_start - w <= f.line <= issue.end + w

    async def matches(self, f: Finding, issue: LabeledIssue) -> bool:
        if self.line_match(f, issue):
            return True
        if self.llm is None:
            return False
        key = hashlib.sha1(
            f"{f.file}|{f.line}|{f.title}|{issue.file}|{issue.line_start}|{issue.description}".encode()
        ).hexdigest()
        if key not in self.cache:
            user = (
                f"Finding: {f.file}:{f.line} {f.title}\n{f.explanation}\n\n"
                f"Known issue: {issue.file}:{issue.line_start}-{issue.end} {issue.description}"
            )
            try:
                out = await chat_validated(
                    self.llm,
                    _Same,
                    [{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
                    model=self.settings.match_judge_model,
                    stage="match",
                )
                self.cache[key] = out.same
            except LLMError:
                return False
            if self.cache_path:
                self.cache_path.write_text(json.dumps(self.cache))
        return self.cache[key]


async def score_pr(
    ex: Example, result: ReviewResult, trace: dict[str, Any], matcher: Matcher
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (per-PR row, per-finding rows for calibration)."""
    comments = [*result.inline, *result.overflow]
    matched_labels: set[int] = set()
    tp = 0
    for rec in comments:
        hit = False
        for i, issue in enumerate(ex.labels.issues):
            if await matcher.matches(rec.finding, issue):
                matched_labels.add(i)
                hit = True
        tp += hit
    finding_rows: list[dict[str, Any]] = []
    for rec in [*comments, *result.discarded]:
        if rec.gate is None:
            continue
        hit = False
        for issue in ex.labels.issues:
            if await matcher.matches(rec.finding, issue):
                hit = True
                break
        finding_rows.append(
            {
                "pr_id": ex.pr_id,
                "finding_id": rec.finding.id,
                "gate_score": rec.gate_score,
                "disposition": rec.disposition,
                "matched_label": hit,
            }
        )
    n_labels = len(ex.labels.issues)
    return (
        {
            "pr_id": ex.pr_id,
            "pipeline": result.pipeline,
            "cost_usd": trace["total_cost_usd"],
            "latency_s": trace["total_latency_s"],
            "n_comments": len(comments),
            "true_positives": tp,
            "labels": n_labels,
            "labels_found": len(matched_labels),
            "skipped": result.route.skip,
            "depth": result.route.depth,
            "has_labels": n_labels > 0,
        },
        finding_rows,
    )


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for pipeline, g in rows.groupby("pipeline"):
        comments = g["n_comments"].sum()
        labels = g["labels"].sum()
        with_labels = g[g["has_labels"]]
        out.append(
            {
                "pipeline": pipeline,
                "prs": len(g),
                "cost_per_pr_usd": g["cost_usd"].mean(),
                "latency_p50_s": g["latency_s"].quantile(0.5),
                "latency_p95_s": g["latency_s"].quantile(0.95),
                "comments": int(comments),
                "precision": g["true_positives"].sum() / comments if comments else float("nan"),
                "recall": g["labels_found"].sum() / labels if labels else float("nan"),
                "skip_rate": g["skipped"].mean(),
                "skipped_with_labels": int(with_labels["skipped"].sum()),
                "downgraded_with_labels": int((with_labels["depth"] == "light").sum()),
            }
        )
    return pd.DataFrame(out)


BUCKETS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
BUCKET_LABELS = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]


def calibration(findings: pd.DataFrame) -> pd.DataFrame:
    if findings.empty:
        return pd.DataFrame(columns=["bucket", "n", "matched", "fraction_matched"])
    f = findings.dropna(subset=["gate_score"]).copy()
    f["bucket"] = pd.cut(f["gate_score"], BUCKETS, labels=BUCKET_LABELS, right=False)
    g = f.groupby("bucket", observed=False)["matched_label"]
    out = pd.DataFrame({"n": g.size(), "matched": g.sum()}).reset_index()
    out["fraction_matched"] = out["matched"] / out["n"].where(out["n"] > 0)
    return out
