"""Routing and gating logic as pure functions. All thresholds come from config."""

from __future__ import annotations

import re
from typing import Literal

from codereview.config import GateThresholds, RoutingThresholds
from codereview.models import GateResult, RouteDecision, TriageResult

ALL_AGENTS = ["correctness", "security", "tests", "architecture", "performance"]


def guardrail_hits(paths: list[str], patterns: list[str]) -> list[str]:
    rx = [re.compile(p, re.I) for p in patterns]
    return sorted({p for p in paths if any(r.search(p) for r in rx)})


def fail_safe_route(hits: list[str], reason: str) -> RouteDecision:
    """Judge failed or was unsure: full deep review with every agent, never a skip."""
    return RouteDecision(
        skip=False,
        depth="deep",
        agents=list(ALL_AGENTS),
        human_required=bool(hits),
        guardrail_hits=hits,
        reasons=[reason],
        failed_safe=True,
    )


def route(
    triage: TriageResult | None,
    paths: list[str],
    th: RoutingThresholds,
    sensitive_patterns: list[str],
    *,
    failure: str | None = None,
) -> RouteDecision:
    hits = guardrail_hits(paths, sensitive_patterns)
    if triage is None:
        return fail_safe_route(hits, f"judge unavailable: {failure or 'unknown error'}")
    if triage.min_confidence() < th.min_confidence:
        return fail_safe_route(
            hits, f"low judge confidence {triage.min_confidence():.2f} < {th.min_confidence}"
        )

    reasons: list[str] = []
    risk = triage.risk.value
    sens = triage.touches_sensitive.value

    if (
        triage.trivial.value > th.skip_trivial_p
        and risk < th.skip_risk_max
        and sens < th.skip_sensitive_p_max
        and not hits
    ):
        return RouteDecision(
            skip=True,
            depth="skip",
            agents=[],
            human_required=False,
            reasons=["trivial, low risk, not sensitive, no guardrail hit"],
        )

    agents = ["correctness"]
    if sens > th.security_sensitive_p or hits:
        agents.append("security")
    if triage.lacks_test_coverage.value > th.tests_p:
        agents.append("tests")
    if triage.changes_public_contract.value > th.architecture_p:
        agents.append("architecture")
    if triage.perf_sensitive.value > th.performance_p:
        agents.append("performance")

    deep = risk >= th.deep_risk_min or len(agents) > th.deep_agent_count_over or bool(hits)
    human = risk >= th.human_risk_min or sens > th.human_sensitive_p or bool(hits)
    if hits:
        reasons.append(f"guardrail paths: {hits}")
    if risk >= th.deep_risk_min:
        reasons.append(f"risk {risk:.2f} >= {th.deep_risk_min}")
    if len(agents) > th.deep_agent_count_over:
        reasons.append(f"{len(agents)} agents > {th.deep_agent_count_over}")
    if human:
        reasons.append("human review required")
    return RouteDecision(
        skip=False,
        depth="deep" if deep else "light",
        agents=agents,
        human_required=human,
        guardrail_hits=hits,
        reasons=reasons or ["default light review"],
    )


GateAction = Literal["discard", "accept", "verify"]


def decide_gate(g: GateResult, th: GateThresholds) -> tuple[GateAction, str]:
    if g.duplicate.value > th.discard_duplicate_p:
        return "discard", f"duplicate p={g.duplicate.value:.2f} > {th.discard_duplicate_p}"
    if g.is_real_defect.value < th.discard_real_p:
        return "discard", f"is_real_defect p={g.is_real_defect.value:.2f} < {th.discard_real_p}"
    if g.actionable.value < th.discard_actionable_p:
        return "discard", f"actionable p={g.actionable.value:.2f} < {th.discard_actionable_p}"
    if (
        g.is_real_defect.value >= th.accept_real_p
        and g.actionable.value >= th.accept_actionable_p
        and g.severity.value >= th.accept_severity_min
    ):
        return "accept", "high-confidence real, actionable, severe enough"
    return "verify", "uncertain band"
