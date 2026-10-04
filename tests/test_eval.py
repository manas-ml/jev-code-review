import json
from pathlib import Path

import pandas as pd

from codereview.eval.dataset import load_dataset
from codereview.eval.report import write_report
from codereview.eval.score import Matcher, calibration, score_pr, summarize
from codereview.judges.mock_judge import MockJevJudge
from codereview.pipeline.run import run_pipeline
from codereview.tracing import Tracer
from conftest import FakeLLM, default_handler

DATASET = Path(__file__).parent / "fixtures" / "eval_dataset"


async def test_dataset_scoring_and_report(settings, tmp_path):
    examples = load_dataset(DATASET)
    assert {e.pr_id for e in examples} == {"pricing-bugfix", "readme-typo"}
    matcher = Matcher(settings)
    rows, frows = [], []
    for ex in examples:
        for p, judge in (("jev", MockJevJudge()), ("baseline", MockJevJudge())):
            t = Tracer(p, ex.pr_id)
            res = await run_pipeline(p, ex.context, FakeLLM(default_handler), judge, settings, t)
            row, f = await score_pr(ex, res, t.to_dict(), matcher)
            rows.append(row)
            if p == "jev":
                frows.extend(f)
    per_pr = pd.DataFrame(rows)
    s = summarize(per_pr).set_index("pipeline")
    assert s.loc["jev", "recall"] == 1.0 and s.loc["jev", "precision"] == 1.0
    assert s.loc["jev", "skip_rate"] == 0.5 and s.loc["baseline", "skip_rate"] == 0.5
    assert s.loc["jev", "skipped_with_labels"] == 0
    cal = calibration(pd.DataFrame(frows))
    jev_cal = cal[cal.pipeline == "jev"]
    assert list(jev_cal["bucket"]) == ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
    assert jev_cal.set_index("bucket").loc["0.8-1.0", "fraction_matched"] == 1.0
    path = write_report(tmp_path, s.reset_index(), per_pr, cal, pd.DataFrame(frows))
    assert "Calibration" in path.read_text()
    assert (tmp_path / "calibration.csv").exists()
    assert json.loads((tmp_path / "report.json").read_text())["summary"]


def test_line_window_match(settings):
    from codereview.eval.dataset import LabeledIssue
    from codereview.models import Finding

    m = Matcher(settings)
    issue = LabeledIssue(file="f", line_start=10, line_end=12)

    def f(line, file="f"):
        return Finding(
            id="1",
            agent="a",
            file=file,
            line=line,
            title="t",
            explanation="e",
            evidence="x",
            category="c",
        )

    assert m.line_match(f(14), issue) and not m.line_match(f(15), issue)  # window 2
    assert m.line_match(f(8), issue) and not m.line_match(f(7), issue)
    assert not m.line_match(f(11, "g"), issue)


def test_summarize_with_repeats_reports_spread():
    rows = []
    for rep, found in ((0, 1), (1, 0)):
        rows.append(
            {
                "pr_id": "p",
                "pipeline": "jev",
                "repeat": rep,
                "cost_usd": 0.01,
                "latency_s": 1.0,
                "n_comments": 1,
                "true_positives": found,
                "decoy_hits": 1 - found,
                "decoys": 1,
                "labels": 1,
                "labels_found": found,
                "skipped": False,
                "depth": "deep",
                "has_labels": True,
            }
        )
    s = summarize(pd.DataFrame(rows)).iloc[0]
    assert s["repeats"] == 2 and s["recall"] == 0.5
    assert s["recall_min"] == 0.0 and s["recall_max"] == 1.0 and s["decoy_hits"] == 0.5


def test_decoy_hit_counted(settings):
    import asyncio

    from codereview.eval.dataset import Example, LabeledIssue, Labels
    from codereview.models import Finding, FindingRecord, ReviewResult, RouteDecision
    from conftest import load

    ex = Example(
        pr_id="p",
        context=load("bugfix"),
        labels=Labels(
            issues=[LabeledIssue(file="f", line_start=50)],
            must_not_flag=[LabeledIssue(file="f", line_start=10)],
        ),
    )
    f = Finding(
        id="1", agent="a", file="f", line=11, title="t", explanation="e", evidence="x", category="c"
    )
    route = RouteDecision(skip=False, depth="deep", agents=[], human_required=False)
    res = ReviewResult(
        pr_id="p",
        pipeline="jev",
        route=route,
        inline=[FindingRecord(finding=f, disposition="accepted_gate")],
    )
    trace = {"total_cost_usd": 0, "total_latency_s": 0}
    row, _ = asyncio.run(score_pr(ex, res, trace, Matcher(settings)))
    assert row["decoy_hits"] == 1 and row["true_positives"] == 0


def test_calibrate_picks_thresholds_and_reports_holdout():
    from codereview.config import GateThresholds
    from codereview.eval.calibrate import calibrate

    rows = []
    for i in range(60):
        good = i % 2 == 0
        rows.append(
            {
                "pr_id": f"pr{i}",
                "pipeline": "jev",
                "is_real_defect": 0.9 if good else 0.6,
                "actionable": 0.9,
                "duplicate": 0.0,
                "severity": 3.0,
                "severity_raw": 3.0,
                "matched_label": good,
                "decoy": (not good) and i % 4 == 1,
            }
        )
    res = calibrate(pd.DataFrame(rows), GateThresholds())["jev"]
    t = res["thresholds"]
    assert 0.6 < t["accept_real_p"] <= 0.9
    assert res["holdout"]["precision"] == 1.0 and res["holdout"]["decoy_accepts"] == 0
    assert t["discard_real_p"] < t["accept_real_p"]
