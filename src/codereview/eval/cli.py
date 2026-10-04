"""`compare` and `calibrate` commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console

from codereview.cli_core import review_context
from codereview.config import Settings, load_settings
from codereview.eval.calibrate import calibrate as run_calibration
from codereview.eval.calibrate import render as render_calibration
from codereview.eval.dataset import Example, load_dataset
from codereview.eval.report import write_report
from codereview.eval.score import Matcher, calibration, score_pr, summarize
from codereview.llm.client import OpenRouterClient
from codereview.models import ReviewResult

console = Console()


def register(app: typer.Typer) -> None:
    @app.command()
    def compare(
        dataset: Annotated[Path, typer.Option(help="eval/dataset directory")] = Path(
            "eval/dataset"
        ),
        pipelines: Annotated[str, typer.Option(help="comma list")] = "jev,baseline",
        out: Annotated[Path, typer.Option()] = Path("runs/compare"),
        use_match_judge: Annotated[bool, typer.Option()] = True,
        repeats: Annotated[int, typer.Option(help="runs per PR per pipeline")] = 3,
        parallel: Annotated[int, typer.Option(help="PR reviews in flight at once")] = 3,
    ) -> None:
        """Run pipelines over a labeled dataset (resumable) and write a comparison report."""
        asyncio.run(
            _compare(dataset, pipelines.split(","), out, use_match_judge, repeats, parallel)
        )

    @app.command()
    def calibrate(
        runs: Annotated[Path, typer.Option(help="a compare --out directory")] = Path(
            "runs/compare"
        ),
    ) -> None:
        """Tune gate thresholds per judge from recorded gate answers (no model calls)."""
        settings = load_settings()
        findings = pd.read_csv(runs / "calibration_findings.csv")
        results = run_calibration(findings, settings.gate)
        if not results:
            console.print("[red]no labeled matches in recorded findings; nothing to calibrate")
            raise typer.Exit(1)
        (runs / "calibration_report.md").write_text(render_calibration(results))
        gate_file = runs / "calibrated_gate.json"
        gate_file.write_text(json.dumps({p: r["thresholds"] for p, r in results.items()}, indent=2))
        console.print(render_calibration(results))
        console.print(
            f"[green]wrote[/green] {gate_file}; use it with GATE_OVERRIDES_FILE={gate_file}"
        )


def run_dir_for(out: Path, pipeline: str, pr_id: str, repeat: int) -> Path:
    # repeat 0 keeps the pre-repeats name so earlier cached runs are reused
    suffix = "" if repeat == 0 else f"-r{repeat}"
    return out / "runs" / f"{pipeline}-{pr_id}{suffix}"


async def _compare(
    dataset: Path,
    pipelines: list[str],
    out: Path,
    use_judge: bool,
    repeats: int,
    parallel: int,
) -> None:
    settings = load_settings()
    examples = load_dataset(dataset)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(ex, p, r) for r in range(repeats) for ex in examples for p in pipelines]
    sem = asyncio.Semaphore(parallel)

    async def run_one(ex: Example, p: str, r: int) -> None:
        run_dir = run_dir_for(out, p, ex.pr_id, r)
        if (run_dir / "review.json").exists():
            return
        async with sem:
            console.print(f"running {p} on {ex.pr_id} (repeat {r})")
            try:
                await review_context(ex.context, p, settings, run_dir)
            except Exception as e:  # one failed review must not sink the whole compare
                console.print(f"[red]failed[/red] {p} on {ex.pr_id} (repeat {r}): {e!r}")

    await asyncio.gather(*(run_one(*j) for j in jobs))
    await _score_all(settings, out, jobs, use_judge)


async def _score_all(
    settings: Settings, out: Path, jobs: list[tuple[Example, str, int]], use_judge: bool
) -> None:
    llm = OpenRouterClient(settings)
    matcher = Matcher(
        settings,
        llm if use_judge and settings.openrouter_api_key else None,
        out / "match_cache.json",
    )
    rows: list[dict[str, Any]] = []
    frows: list[dict[str, Any]] = []
    missing = 0
    for ex, p, r in jobs:
        run_dir = run_dir_for(out, p, ex.pr_id, r)
        if not (run_dir / "review.json").exists():
            missing += 1
            continue
        result = ReviewResult.model_validate(json.loads((run_dir / "review.json").read_text()))
        trace = json.loads((run_dir / "trace.json").read_text())
        row, f = await score_pr(ex, result, trace, matcher, repeat=r)
        rows.append(row)
        frows.extend(f)
    await llm.http.aclose()
    if missing:
        console.print(f"[yellow]{missing} run(s) missing (failed); excluded from the report")
    per_pr = pd.DataFrame(rows)
    findings = pd.DataFrame(frows)
    path = write_report(out, summarize(per_pr), per_pr, calibration(findings), findings)
    console.print(f"[green]report:[/green] {path}")
