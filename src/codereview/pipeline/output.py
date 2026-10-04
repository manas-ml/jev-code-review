"""Write review.json / review.md / trace.json for a run."""

from __future__ import annotations

import json
from pathlib import Path

from codereview.models import FindingRecord, ReviewResult
from codereview.tracing import Tracer, redact


def _line(r: FindingRecord) -> str:
    f = r.finding
    fix = f"\n  Suggested fix: {f.suggested_fix}" if f.suggested_fix else ""
    return f"- **{f.title}** (`{f.file}:{f.line}`, {f.agent}) - {f.explanation}{fix}"


def render_markdown(result: ReviewResult) -> str:
    rt = result.route
    out = [
        f"# Review: {result.pr_id} ({result.pipeline})",
        "",
        f"Route: **{rt.depth}**, agents: {', '.join(rt.agents) or 'none'}"
        + (", **human review required**" if rt.human_required else "")
        + (" (fail-safe)" if rt.failed_safe else ""),
        "",
        result.summary,
        "",
    ]
    if result.inline:
        out += ["## Findings", *map(_line, result.inline), ""]
    if result.overflow:
        out += ["## Additional findings", *map(_line, result.overflow), ""]
    return redact("\n".join(out))


def write_outputs(run_dir: Path, result: ReviewResult, tracer: Tracer) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "review.json").write_text(
        redact(json.dumps(result.model_dump(), indent=2, default=str))
    )
    (run_dir / "review.md").write_text(render_markdown(result))
    tracer.write(run_dir / "trace.json")


def github_comments(result: ReviewResult) -> list[dict[str, object]]:
    return [
        {
            "path": r.finding.file,
            "line": r.finding.line,
            "side": "RIGHT",
            "body": redact(f"**{r.finding.title}**\n\n{r.finding.explanation}"),
        }
        for r in result.inline
    ]
