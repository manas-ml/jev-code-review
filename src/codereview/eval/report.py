"""Markdown + JSON comparison report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


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
        "A = jev, B = baseline (LLM judge).",
        "",
        "## Summary",
        summary.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Calibration (pipeline A gate `is_real_defect` probability vs labeled match)",
        calib.to_markdown(index=False, floatfmt=".3f"),
        "",
        "Notes: small datasets give noisy precision/recall; skipped_with_labels and "
        "downgraded_with_labels count A's missed-issue risk.",
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
