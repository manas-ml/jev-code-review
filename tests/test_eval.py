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
    assert list(cal["bucket"]) == ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
    assert cal.set_index("bucket").loc["0.8-1.0", "fraction_matched"] == 1.0
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

    assert m.line_match(f(17), issue) and not m.line_match(f(18), issue)
    assert m.line_match(f(5), issue) and not m.line_match(f(4), issue)
    assert not m.line_match(f(11, "g"), issue)
