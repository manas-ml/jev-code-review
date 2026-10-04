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
from codereview.models import Finding, FindingRecord, ReviewResult

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
    ex: Example,
    result: ReviewResult,
    trace: dict[str, Any],
    matcher: Matcher,
    repeat: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (per-PR row, per-finding rows for calibration).

    A comment is a true positive if it matches a labeled issue; a decoy hit if it does not
    match any issue but lands on a must-not-flag region (line match only, no LLM judge).
    """
    comments = [*result.inline, *result.overflow]
    matched_labels: set[int] = set()
    tp = decoys = 0

    async def match_issue(rec: FindingRecord) -> list[int]:
        return [
            i
            for i, issue in enumerate(ex.labels.issues)
            if await matcher.matches(rec.finding, issue)
        ]

    def on_decoy(rec: FindingRecord) -> bool:
        return any(matcher.line_match(rec.finding, d) for d in ex.labels.must_not_flag)

    for rec in comments:
        hits = await match_issue(rec)
        matched_labels.update(hits)
        if hits:
            tp += 1
        elif on_decoy(rec):
            decoys += 1
    finding_rows: list[dict[str, Any]] = []
    for rec in [*comments, *result.discarded]:
        if rec.gate is None:
            continue
        hit = bool(await match_issue(rec))
        finding_rows.append(
            {
                "pr_id": ex.pr_id,
                "pipeline": result.pipeline,
                "repeat": repeat,
                "finding_id": rec.finding.id,
                "agent": rec.finding.agent,
                "gate_score": rec.gate_score,
                "is_real_defect": rec.gate.is_real_defect.value,
                "actionable": rec.gate.actionable.value,
                "duplicate": rec.gate.duplicate.value,
                "severity_raw": rec.gate.severity.value,
                "severity": rec.severity,
                "disposition": rec.disposition,
                "matched_label": hit,
                "decoy": (not hit) and on_decoy(rec),
            }
        )
    n_labels = len(ex.labels.issues)
    return (
        {
            "pr_id": ex.pr_id,
            "pipeline": result.pipeline,
            "repeat": repeat,
            "cost_usd": trace["total_cost_usd"],
            "latency_s": trace["total_latency_s"],
            "n_comments": len(comments),
            "true_positives": tp,
            "decoy_hits": decoys,
            "decoys": len(ex.labels.must_not_flag),
            "labels": n_labels,
            "labels_found": len(matched_labels),
            "skipped": result.route.skip,
            "depth": result.route.depth,
            "has_labels": n_labels > 0,
        },
        finding_rows,
    )


def _metrics(g: pd.DataFrame) -> dict[str, float]:
    comments = g["n_comments"].sum()
    labels = g["labels"].sum()
    with_labels = g[g["has_labels"]]
    return {
        "cost_per_pr_usd": float(g["cost_usd"].mean()),
        "latency_p50_s": float(g["latency_s"].quantile(0.5)),
        "latency_p95_s": float(g["latency_s"].quantile(0.95)),
        "comments": float(comments),
        "precision": g["true_positives"].sum() / comments if comments else float("nan"),
        "recall": g["labels_found"].sum() / labels if labels else float("nan"),
        "decoy_hits": float(g["decoy_hits"].sum()),
        "skip_rate": float(g["skipped"].mean()),
        "skipped_with_labels": float(with_labels["skipped"].sum()),
        "downgraded_with_labels": float((with_labels["depth"] == "light").sum()),
    }


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per pipeline. With repeats, each metric is the mean over repeats, and
    `<metric>_min` / `<metric>_max` give the spread for the headline metrics."""
    if "repeat" not in rows:
        rows = rows.assign(repeat=0)
    out = []
    for pipeline, g in rows.groupby("pipeline"):
        per = pd.DataFrame([_metrics(r) for _, r in g.groupby("repeat")])
        row: dict[str, Any] = {
            "pipeline": pipeline,
            "prs": g["pr_id"].nunique(),
            "repeats": len(per),
        }
        for col in per.columns:
            row[col] = per[col].mean()
        for col in ("precision", "recall", "decoy_hits"):
            row[f"{col}_min"] = per[col].min()
            row[f"{col}_max"] = per[col].max()
        out.append(row)
    return pd.DataFrame(out)


BUCKETS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
BUCKET_LABELS = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]


def calibration(findings: pd.DataFrame) -> pd.DataFrame:
    """Per pipeline: for each gate `is_real_defect` bucket, the fraction of findings that
    matched a labeled issue (and how many landed on decoys)."""
    cols = ["pipeline", "bucket", "n", "matched", "decoys", "fraction_matched"]
    if findings.empty:
        return pd.DataFrame(columns=cols)
    f = findings.dropna(subset=["gate_score"]).copy()
    if "pipeline" not in f:
        f["pipeline"] = "jev"
    if "decoy" not in f:
        f["decoy"] = False
    f["bucket"] = pd.cut(f["gate_score"], BUCKETS, labels=BUCKET_LABELS, right=False)
    g = f.groupby(["pipeline", "bucket"], observed=False)
    out = pd.DataFrame(
        {"n": g.size(), "matched": g["matched_label"].sum(), "decoys": g["decoy"].sum()}
    ).reset_index()
    out["fraction_matched"] = out["matched"] / out["n"].where(out["n"] > 0)
    return out[cols]
