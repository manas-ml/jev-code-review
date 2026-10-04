"""Shared helper used by the CLI commands."""

from __future__ import annotations

from pathlib import Path

from codereview.config import Settings
from codereview.judges.factory import make_judge
from codereview.llm.client import OpenRouterClient
from codereview.models import ReviewContext, ReviewResult
from codereview.pipeline.output import write_outputs
from codereview.pipeline.run import run_pipeline
from codereview.tracing import Tracer


async def review_context(
    ctx: ReviewContext, pipeline: str, settings: Settings, run_dir: Path
) -> ReviewResult:
    llm = OpenRouterClient(settings)
    judge = make_judge(pipeline, settings, llm, llm.http)
    tracer = Tracer(pipeline, ctx.pr_id)
    result = await run_pipeline(pipeline, ctx, llm, judge, settings, tracer)
    write_outputs(run_dir, result, tracer)
    await llm.http.aclose()
    return result
