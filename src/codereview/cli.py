"""CLI: review, eval-bootstrap, compare."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from codereview.cli_core import review_context
from codereview.config import load_settings
from codereview.context.builder import load_local
from codereview.github.client import fetch_pr, post_review
from codereview.models import ReviewResult
from codereview.pipeline.output import github_comments
from codereview.pipeline.run import PIPELINES

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def review(
    pr: Annotated[str | None, typer.Option(help="GitHub PR URL")] = None,
    diff: Annotated[Path | None, typer.Option(help="Local unified diff")] = None,
    meta: Annotated[Path | None, typer.Option(help="Local meta.json (title, description)")] = None,
    pipeline: Annotated[str, typer.Option(help="jev | baseline | naive")] = "jev",
    repo: Annotated[Path | None, typer.Option(help="Checked-out repo for agent tools")] = None,
    post: Annotated[bool, typer.Option("--post/--dry-run")] = False,
    run_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Review one PR. Dry run by default (writes review.md/json and trace.json)."""
    if pipeline not in PIPELINES:
        raise typer.BadParameter(f"pipeline must be one of {PIPELINES}")
    settings = load_settings()

    async def go() -> ReviewResult:
        if pr:
            ctx = await fetch_pr(pr, settings.github_token, str(repo) if repo else None)
        elif diff and meta:
            ctx = load_local(diff, meta, repo_root=str(repo) if repo else None)
        else:
            raise typer.BadParameter("provide --pr, or --diff and --meta")
        rid = run_id or f"{ctx.pr_id}-{pipeline}-{int(time.time())}"
        run_dir = Path("runs") / rid
        result = await review_context(ctx, pipeline, settings, run_dir)
        console.print(f"[green]wrote[/green] {run_dir}/(review.md, review.json, trace.json)")
        if post and pr:
            await post_review(pr, settings.github_token, result.summary, github_comments(result))
            console.print("posted review")
        return result

    result = asyncio.run(go())
    console.print(result.summary)


from codereview.eval.cli import register  # noqa: E402

register(app)
