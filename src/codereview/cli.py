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
from codereview.github.client import commentable_lines, fetch_pr, post_review
from codereview.models import ReviewResult
from codereview.pipeline.output import render_review_body, split_for_posting
from codereview.pipeline.run import PIPELINES

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def review(
    pr: Annotated[str | None, typer.Option(help="GitHub PR URL")] = None,
    diff: Annotated[Path | None, typer.Option(help="Local unified diff")] = None,
    meta: Annotated[Path | None, typer.Option(help="Local meta.json (title, description)")] = None,
    pipeline: Annotated[str, typer.Option(help="jev (the only pipeline)")] = "jev",
    repo: Annotated[Path | None, typer.Option(help="Checked-out repo for agent tools")] = None,
    post: Annotated[bool, typer.Option("--post/--dry-run")] = False,
    run_id: Annotated[str | None, typer.Option()] = None,
    min_severity: Annotated[
        float | None,
        typer.Option(help="Post inline only findings with severity >= this (0-4). Default 3."),
    ] = None,
) -> None:
    """Review one PR. Dry run by default (writes review.md/json and trace.json)."""
    if pipeline not in PIPELINES:
        raise typer.BadParameter(f"pipeline must be one of {PIPELINES}")
    settings = load_settings()
    if not settings.openrouter_api_key and settings.jev_mode == "real":
        console.print("[red]OPENROUTER_API_KEY is not set[/red] (needed for agents and the judge)")
        raise typer.Exit(2)
    if min_severity is not None:
        settings.post_min_severity = min_severity

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
            floor = settings.post_min_severity
            comments, rest = split_for_posting(
                result, floor, commentable_lines(ctx.files), settings.max_inline_comments
            )
            if not (comments or rest or result.route.human_required or result.warnings):
                console.print("nothing to post (no findings, no human-review flag)")
                return result
            body = render_review_body(result, len(comments), rest, floor)
            # fallback if GitHub rejects inline comments: put every finding in the body
            fallback = render_review_body(result, 0, [*result.inline, *result.overflow], floor)
            ok = await post_review(pr, settings.github_token, body, comments, fallback)
            console.print(
                f"posted review: {len(comments)} inline, {len(rest)} in body"
                + ("" if ok or not comments else " (inline rejected; posted as body)")
            )
        return result

    result = asyncio.run(go())
    console.print(result.summary)
    for w in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
    if result.errors:
        for e in result.errors:
            console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(1)


from codereview.eval.cli import register  # noqa: E402

register(app)
