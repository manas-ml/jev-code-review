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
    out += [f"- WARNING: {w}" for w in [*result.errors, *result.warnings]]
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


BODY_LIMIT = 60_000


def _severity(r: FindingRecord) -> float:
    return r.gate.severity.value if r.gate else 0.0


def split_for_posting(
    result: ReviewResult,
    min_severity: float,
    commentable: dict[str, set[int]] | None,
    cap: int,
) -> tuple[list[dict[str, object]], list[FindingRecord]]:
    """Returns (inline GitHub comments, ranked findings that are NOT posted inline).

    Inline only if severity >= min_severity, the line is visible in the diff, and the cap
    isn't reached. Everything else goes in the review body so nothing is lost.
    """
    comments: list[dict[str, object]] = []
    rest: list[FindingRecord] = []
    for r in [*result.inline, *result.overflow]:
        f = r.finding
        visible = commentable is None or f.line in commentable.get(f.file, set())
        if _severity(r) >= min_severity and visible and len(comments) < cap:
            comments.append(
                {
                    "path": f.file,
                    "line": f.line,
                    "side": "RIGHT",
                    "body": redact(
                        f"**#{r.rank} {severity_label(r)}: {f.title}**\n\n{f.explanation}"
                        + (f"\n\n**Suggested fix:** {f.suggested_fix}" if f.suggested_fix else "")
                    ),
                }
            )
        else:
            rest.append(r)
    return comments, rest


def render_review_body(
    result: ReviewResult, n_inline: int, rest: list[FindingRecord], min_severity: float
) -> str:
    rt = result.route
    ranked = [*result.inline, *result.overflow]
    out = [
        "## Automated review (Jev gate)" if result.pipeline == "jev" else "## Automated review",
        "",
        f"- Route: **{rt.depth}**, agents: {', '.join(rt.agents) or 'none'}"
        + (" (fail-safe)" if rt.failed_safe else ""),
        f"- Human review required: **{'yes' if rt.human_required else 'no'}**",
        f"- {len(ranked)} finding(s) after filtering; {n_inline} posted inline "
        f"(severity >= {min_severity:g}).",
    ]
    out += [f"- WARNING: {w}" for w in [*result.errors, *result.warnings]]
    if ranked:
        out += ["", "### Ranking", "", *_table(ranked)]
    if rest:
        out += ["", "<details><summary>Findings not posted inline</summary>", ""]
        out += [_line(r) for r in rest]
        out += ["", "</details>"]
    return redact("\n".join(out))[:BODY_LIMIT]
