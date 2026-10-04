"""Markdown + JSON comparison report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _per_pr_table(per_pr: pd.DataFrame) -> str:
    if per_pr.empty:
        return "(no runs)"
    cols = ["n_comments", "true_positives", "decoy_hits", "labels", "labels_found", "cost_usd"]
    t = per_pr.pivot_table(index="pr_id", columns="pipeline", values=cols, aggfunc="mean")
    t.columns = pd.Index([f"{col[0]}:{col[1]}" for col in t.columns])
    return str(t.reset_index().to_markdown(index=False, floatfmt=".2f"))


def write_report(
    out_dir: Path,
    summary: pd.DataFrame,
    per_pr: pd.DataFrame,
    calib: pd.DataFrame,
    findings: pd.DataFrame,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = [
        "# Pipeline comparison",
        "",
        "Jev pipeline. Metrics are means over repeats; `_min`/`_max` give "
        "the spread across repeats.",
        "",
        "## Summary",
        summary.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Per-PR (mean over repeats)",
        _per_pr_table(per_pr),
        "",
        "## Calibration (gate `is_real_defect` probability vs labeled match)",
        calib.to_markdown(index=False, floatfmt=".3f"),
        "",
        "Notes: `decoy_hits` counts comments on code that looks wrong but is correct "
        "(`must_not_flag`); skipped_with_labels and downgraded_with_labels count missed-issue "
        "risk from triage. Small datasets give noisy numbers.",
    ]
    path = out_dir / "report.md"
    path.write_text("\n".join(md))
    (out_dir / "report.json").write_text(
        json.dumps(
            {
                "summary": summary.to_dict(orient="records"),
                "calibration": calib.to_dict(orient="records"),
            },
            indent=2,
            default=str,
        )
    )
    per_pr.to_csv(out_dir / "per_pr.csv", index=False)
    findings.to_csv(out_dir / "calibration_findings.csv", index=False)
    calib.to_csv(out_dir / "calibration.csv", index=False)
    return path
