"""`compare` command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console

from codereview.cli_core import review_context
from codereview.config import load_settings
from codereview.eval.dataset import load_dataset
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
        pipelines: Annotated[str, typer.Option(help="comma list")] = "jev,baseline,naive",
        out: Annotated[Path, typer.Option()] = Path("runs/compare"),
        use_match_judge: Annotated[bool, typer.Option()] = True,
    ) -> None:
        """Run pipelines over a labeled dataset (resumable) and write a comparison report."""
        asyncio.run(_compare(dataset, pipelines.split(","), out, use_match_judge))


async def _compare(dataset: Path, pipelines: list[str], out: Path, use_judge: bool) -> None:
    settings = load_settings()
    examples = load_dataset(dataset)
    llm = OpenRouterClient(settings)
    matcher = Matcher(
        settings,
        llm if use_judge and settings.openrouter_api_key else None,
        out / "match_cache.json",
    )
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    frows: list[dict[str, Any]] = []
    for ex in examples:
        for p in pipelines:
            run_dir = out / "runs" / f"{p}-{ex.pr_id}"
            if not (run_dir / "review.json").exists():
                console.print(f"running {p} on {ex.pr_id}")
                await review_context(ex.context, p, settings, run_dir)
            else:
                console.print(f"cached  {p} on {ex.pr_id}")
            result = ReviewResult.model_validate(json.loads((run_dir / "review.json").read_text()))
            trace = json.loads((run_dir / "trace.json").read_text())
            row, f = await score_pr(ex, result, trace, matcher)
            rows.append(row)
            if p == "jev":
                frows.extend(f)
    await llm.http.aclose()
    per_pr = pd.DataFrame(rows)
    findings = pd.DataFrame(frows)
    path = write_report(out, summarize(per_pr), per_pr, calibration(findings), findings)
    console.print(f"[green]report:[/green] {path}")
