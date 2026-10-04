"""Write review.json / review.md / trace.json for a run."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from codereview.judges.jev.questions import SEVERITY_LEVELS
from codereview.models import FindingRecord, ReviewResult
from codereview.tracing import Tracer, redact


def severity_label(r: FindingRecord) -> str:
    if r.gate is None:
        return "unscored"
    idx = max(0, min(len(SEVERITY_LEVELS) - 1, round(r.gate.severity.value)))
    return SEVERITY_LEVELS[idx]


def _sev(r: FindingRecord) -> str:
    return f"{r.gate.severity.value:.1f}" if r.gate else "-"


def _conf(r: FindingRecord) -> str:
    return f"{r.gate_score:.2f}" if r.gate_score is not None else "-"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(rows: list[FindingRecord]) -> list[str]:
    out = [
        "| Rank | Severity | Score (0-4) | Confidence | Location | Finding | Agent | Verified |",
        "|-----:|----------|------------:|-----------:|----------|---------|-------|----------|",
    ]
    for r in rows:
        f = r.finding
        verified = "yes" if r.disposition == "accepted_verified" else "gate"
        out.append(
            f"| {r.rank} | {severity_label(r)} | {_sev(r)} | {_conf(r)} | "
            f"`{f.file}:{f.line}` | {_cell(f.title)} | {f.agent} | {verified} |"
        )
    return out


def _line(r: FindingRecord) -> str:
    f = r.finding
    fix = f"\n  Suggested fix: {f.suggested_fix}" if f.suggested_fix else ""
    return (
        f"{r.rank}. **{f.title}** (`{f.file}:{f.line}`, {severity_label(r)}, {f.agent}) "
        f"- {f.explanation}{fix}"
    )


def render_markdown(result: ReviewResult) -> str:
    rt = result.route
    ranked = [*result.inline, *result.overflow]
    sev_counts = Counter(severity_label(r) for r in ranked)
    reasons = Counter(r.disposition for r in result.discarded)
    out = [
        f"# Review: {result.pr_id} ({result.pipeline})",
        "",
        "## Summary",
        f"- Route: **{rt.depth}**, agents: {', '.join(rt.agents) or 'none'}"
        + (" (fail-safe)" if rt.failed_safe else ""),
        f"- Human review required: **{'yes' if rt.human_required else 'no'}**",
        f"- Findings reported: **{len(ranked)}** "
        f"({len(result.inline)} inline, {len(result.overflow)} overflow)",
    ]
    if ranked:
        out.append(
            "- By severity: "
            + ", ".join(
                f"{n} {lvl}"
                for lvl in (*reversed(SEVERITY_LEVELS), "unscored")
                if (n := sev_counts.get(lvl, 0))
            )
        )
    if result.discarded:
        out.append(
            "- Discarded: "
            + ", ".join(f"{n} {d.replace('_', ' ')}" for d, n in sorted(reasons.items()))
        )
    if not ranked:
        out += ["", result.summary, ""]
        return redact("\n".join(out))
    out += [
        "",
        "## Ranking",
        "Ordered by severity, then confidence (gate `is_real_defect` probability).",
        "",
        *_table(ranked),
        "",
        "## Inline findings",
        *map(_line, result.inline),
        "",
    ]
    if result.overflow:
        out += ["## Additional findings", *map(_line, result.overflow), ""]
    if result.discarded:
        out += [
            "## Discarded",
            *(
                f"- `{r.finding.file}:{r.finding.line}` {_cell(r.finding.title)}"
                f" - {r.disposition.replace('_', ' ')}: {_cell(r.reason)[:140]}"
                for r in result.discarded
            ),
            "",
        ]
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
