"""Orchestrates one PR through a chosen pipeline.

Pipelines share all code except the judge:
  A "jev"      JevJudge for triage + gate
  B "baseline" LLM judge for triage + gate
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from codereview.agents import REGISTRY
from codereview.agents.base import run_agent
from codereview.config import Settings
from codereview.context.builder import file_chunks, triage_state
from codereview.judges.base import Judge
from codereview.llm.client import LLM
from codereview.models import (
    FileDiff,
    Finding,
    FindingRecord,
    GateResult,
    GateState,
    ReviewContext,
    ReviewResult,
    TriageResult,
)
from codereview.pipeline.dedupe import dedupe
from codereview.pipeline.rank import rank_and_cap
from codereview.pipeline.routing import decide_gate, route
from codereview.pipeline.verifier import verify_finding
from codereview.tracing import Tracer

PIPELINES = ("jev", "baseline")


def code_window(ctx: ReviewContext, f: Finding, radius: int = 30) -> str:
    """~60 lines of context: from the checkout if available, else the file's diff."""
    if ctx.repo_root:
        try:
            p = (Path(ctx.repo_root) / f.file).resolve()
            if Path(ctx.repo_root).resolve() in p.parents and p.is_file():
                lines = p.read_text(errors="replace").splitlines()
                lo = max(0, f.line - 1 - radius)
                return "\n".join(
                    f"{i}: {ln}" for i, ln in enumerate(lines[lo : f.line + radius], lo + 1)
                )
        except OSError:
            pass
    for fd in ctx.files:
        if fd.path == f.file:
            return fd.patch
    return ""


async def run_pipeline(
    pipeline: str,
    ctx: ReviewContext,
    llm: LLM,
    judge: Judge,
    settings: Settings,
    tracer: Tracer,
) -> ReviewResult:
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline {pipeline}")
    sem = asyncio.Semaphore(settings.max_concurrency)
    paths = [f.path for f in ctx.files]

    # ---- triage / route
    async with tracer.stage("triage"):
        triage: TriageResult | None = None
        failure = None
        try:
            triage = await judge.triage(triage_state(ctx, settings.triage_char_budget), tracer)
        except Exception as e:  # any judge failure fails safe to a deep review
            failure = repr(e)
        decision = route(
            triage, paths, settings.routing, settings.sensitive_path_patterns, failure=failure
        )
    tracer.extra["route"] = decision.model_dump()
    tracer.extra["triage"] = triage.model_dump() if triage else None

    result = ReviewResult(pr_id=ctx.pr_id, pipeline=pipeline, route=decision)
    if decision.skip:
        result.summary = "Skipped: " + "; ".join(decision.reasons)
        return result

    # ---- specialist agents (parallel)
    use_tools = decision.depth == "deep"

    async def one(name: str, files: list[FileDiff]) -> list[Finding]:
        async with sem:
            return await run_agent(
                REGISTRY[name], ctx, files, llm, settings, tracer, use_tools=use_tools
            )

    async with tracer.stage("agents"):
        chunks = file_chunks(ctx)
        batches = await asyncio.gather(*(one(a, c) for a in decision.agents for c in chunks))
    candidates = [f for b in batches for f in b]
    tracer.extra["n_candidates"] = len(candidates)

    # ---- gate
    records: list[FindingRecord] = []
    discarded: list[FindingRecord] = []
    async with tracer.stage("gate"):

        async def gate_one(idx: int, f: Finding) -> FindingRecord:
            state = GateState(
                finding=f,
                code_window=code_window(ctx, f),
                pr_title=ctx.title,
                existing_comments=ctx.existing_comments,
                # only earlier findings, so one of a duplicate cluster survives
                other_findings=candidates[:idx],
            )
            g: GateResult | None = None
            err = ""
            try:
                async with sem:
                    g = await judge.gate(state, tracer)
            except Exception as e:
                err = repr(e)
            if g is None:  # fail safe: escalate to the verifier, never silently discard
                return await _verify(f, state, None, f"gate failed ({err}); escalated")
            action, why = decide_gate(g, settings.gate)
            if action == "discard":
                return FindingRecord(
                    finding=f,
                    gate=g,
                    gate_score=g.is_real_defect.value,
                    disposition="discarded_gate",
                    reason=why,
                )
            if action == "accept":
                return FindingRecord(
                    finding=f,
                    gate=g,
                    gate_score=g.is_real_defect.value,
                    disposition="accepted_gate",
                    reason=why,
                )
            return await _verify(f, state, g, why)

        async def _verify(
            f: Finding, state: GateState, g: GateResult | None, why: str
        ) -> FindingRecord:
            async with sem:
                v = await verify_finding(
                    f, ctx, state.code_window, llm, settings, tracer, use_tools=use_tools
                )
            score = g.is_real_defect.value if g else None
            if v.verdict == "confirmed":
                return FindingRecord(
                    finding=f,
                    gate=g,
                    gate_score=score,
                    disposition="accepted_verified",
                    reason=f"{why}; verifier confirmed: {v.reasoning}",
                )
            disp = "discarded_verifier" if v.verdict == "refuted" else "dropped_unclear"
            return FindingRecord(
                finding=f,
                gate=g,
                gate_score=score,
                disposition=disp,
                reason=f"{why}; verifier {v.verdict}: {v.reasoning}",
            )

        gated = await asyncio.gather(*(gate_one(i, f) for i, f in enumerate(candidates)))
        for r in gated:
            (records if r.disposition.startswith("accepted") else discarded).append(r)

    # ---- dedupe, rank, cap
    kept, deduped = dedupe(records, settings.dedupe_line_window)
    inline, overflow = rank_and_cap(kept, settings.max_inline_comments)
    result.inline, result.overflow = inline, overflow
    result.discarded = discarded + deduped
    result.summary = (
        f"{len(candidates)} candidate findings -> {len(inline)} inline, "
        f"{len(overflow)} overflow, {len(result.discarded)} discarded."
    )
    tracer.extra["findings"] = [r.model_dump() for r in [*inline, *overflow, *result.discarded]]
    return result
