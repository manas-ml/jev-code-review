"""Gate threshold calibration from recorded gate answers (no new model calls).

Replays the gate's auto-accept rule over the findings `compare` already gated
(`calibration_findings.csv`): a finding is accepted if
    duplicate <= discard_duplicate_p  and  is_real_defect >= accept_real_p
    and actionable >= accept_actionable_p  and  severity >= accept_severity_min.
A grid search on a train split picks, per pipeline, the setting with the best F1 against
labeled issues, then fewest decoy accepts. The held-out split shows whether it generalizes.

Limits: the verifier band cannot be replayed (its verdicts depend on the thresholds), so this
calibrates auto-accept only; discard thresholds are placed below it to leave a verify band.
Recall here is finding-level (accepted matched findings / all matched findings).
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import Any

import pandas as pd

from codereview.config import GateThresholds

REAL_GRID = [round(0.30 + 0.05 * i, 2) for i in range(14)]  # 0.30 .. 0.95
ACT_GRID = [round(0.2 + 0.1 * i, 1) for i in range(8)]  # 0.2 .. 0.9
SEV_GRID = [0.0, 1.0, 2.0, 3.0]


@dataclass
class Score:
    precision: float
    recall: float
    f1: float
    accepted: int
    decoy_accepts: int

    def as_dict(self) -> dict[str, float]:
        return self.__dict__.copy()


def is_holdout(pr_id: str, every: int = 3) -> bool:
    return int(hashlib.sha1(pr_id.encode()).hexdigest(), 16) % every == 0


def _evaluate(df: pd.DataFrame, real: float, act: float, sev: float, dup: float) -> Score:
    sev_col = df["severity"].fillna(df["severity_raw"])
    acc = (
        (df["duplicate"] <= dup)
        & (df["is_real_defect"] >= real)
        & (df["actionable"] >= act)
        & (sev_col >= sev)
    )
    pos = df["matched_label"].astype(bool)
    tp = int((acc & pos).sum())
    n_acc = int(acc.sum())
    n_pos = int(pos.sum())
    p = tp / n_acc if n_acc else 0.0
    r = tp / n_pos if n_pos else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    decoys = int((acc & df["decoy"].astype(bool)).sum())
    return Score(p, r, f1, n_acc, decoys)


def calibrate_one(df: pd.DataFrame, defaults: GateThresholds) -> dict[str, Any]:
    train = df[~df["pr_id"].map(is_holdout)]
    test = df[df["pr_id"].map(is_holdout)]
    dup = defaults.discard_duplicate_p
    best: tuple[tuple[float, int, float], tuple[float, float, float]] | None = None
    for real, act, sev in itertools.product(REAL_GRID, ACT_GRID, SEV_GRID):
        s = _evaluate(train, real, act, sev, dup)
        key = (s.f1, -s.decoy_accepts, s.precision)
        if best is None or key > best[0]:
            best = (key, (real, act, sev))
    assert best is not None
    real, act, sev = best[1]
    tuned = defaults.model_copy(
        update={
            "accept_real_p": real,
            "accept_actionable_p": act,
            "accept_severity_min": int(sev),
            # keep a verify band below auto-accept
            "discard_real_p": round(min(defaults.discard_real_p, max(0.0, real - 0.3)), 2),
            "discard_actionable_p": round(
                min(defaults.discard_actionable_p, max(0.0, act - 0.3)), 2
            ),
        }
    )
    d_real, d_act, d_sev = (
        defaults.accept_real_p,
        defaults.accept_actionable_p,
        float(defaults.accept_severity_min),
    )
    return {
        "thresholds": tuned.model_dump(),
        "n_train": len(train),
        "n_holdout": len(test),
        "train": _evaluate(train, real, act, sev, dup).as_dict(),
        "holdout": _evaluate(test, real, act, sev, dup).as_dict(),
        "holdout_with_defaults": _evaluate(test, d_real, d_act, d_sev, dup).as_dict(),
    }


def calibrate(findings: pd.DataFrame, defaults: GateThresholds) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pipeline, g in findings.groupby("pipeline"):
        if g["matched_label"].astype(bool).sum() == 0:
            continue  # nothing to learn from
        out[str(pipeline)] = calibrate_one(g, defaults)
    return out


def render(results: dict[str, dict[str, Any]]) -> str:
    lines = ["# Gate calibration (auto-accept rule, replayed from recorded answers)", ""]
    for pipeline, r in results.items():
        t = r["thresholds"]
        lines += [
            f"## {pipeline}",
            f"accept: is_real_defect >= {t['accept_real_p']}, actionable >= "
            f"{t['accept_actionable_p']}, severity >= {t['accept_severity_min']}; "
            f"discard below real {t['discard_real_p']} / actionable {t['discard_actionable_p']}",
            f"train n={r['n_train']}, holdout n={r['n_holdout']}",
            "",
            "| split | precision | recall | f1 | accepted | decoy accepts |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name in ("train", "holdout", "holdout_with_defaults"):
            s = r[name]
            lines.append(
                f"| {name} | {s['precision']:.2f} | {s['recall']:.2f} | {s['f1']:.2f} | "
                f"{s['accepted']} | {s['decoy_accepts']} |"
            )
        lines.append("")
    return "\n".join(lines)
