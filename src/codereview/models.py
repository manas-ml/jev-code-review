"""Shared pydantic models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FileDiff(BaseModel):
    path: str
    additions: int = 0
    deletions: int = 0
    patch: str = ""


class StaticFacts(BaseModel):
    tool: str
    output: str


class ReviewContext(BaseModel):
    pr_id: str
    title: str
    description: str = ""
    files: list[FileDiff]
    existing_comments: list[ExistingComment] = Field(default_factory=list)
    static_facts: list[StaticFacts] = Field(default_factory=list)
    repo_root: str | None = None

    @property
    def churn(self) -> int:
        return sum(f.additions + f.deletions for f in self.files)


class ExistingComment(BaseModel):
    file: str
    line: int | None = None
    body: str


class Finding(BaseModel):
    id: str
    agent: str
    file: str
    line: int
    title: str
    explanation: str
    evidence: str
    suggested_fix: str | None = None
    category: str


ChangeType = Literal[
    "feature",
    "bugfix",
    "refactor",
    "dependency_bump",
    "config_infra",
    "docs_only",
    "tests_only",
    "generated_or_vendored",
]


class Scored(BaseModel):
    """A probability or ordered-level answer with the judge's confidence."""

    value: float
    confidence: float = 1.0


class TriageState(BaseModel):
    context: ReviewContext
    text: str
    """Rendered, delimited, truncated state sent to the judge."""


class TriageResult(BaseModel):
    change_type: ChangeType
    change_type_confidence: float = 1.0
    risk: Scored
    trivial: Scored
    touches_sensitive: Scored
    changes_public_contract: Scored
    lacks_test_coverage: Scored
    perf_sensitive: Scored

    def min_confidence(self) -> float:
        return min(
            self.change_type_confidence,
            self.risk.confidence,
            self.trivial.confidence,
            self.touches_sensitive.confidence,
            self.changes_public_contract.confidence,
            self.lacks_test_coverage.confidence,
            self.perf_sensitive.confidence,
        )


class GateState(BaseModel):
    finding: Finding
    code_window: str
    pr_title: str
    existing_comments: list[ExistingComment] = Field(default_factory=list)


class GateResult(BaseModel):
    is_real_defect: Scored
    severity: Scored
    actionable: Scored
    duplicate: Scored


class RouteDecision(BaseModel):
    skip: bool
    depth: Literal["skip", "light", "deep"]
    agents: list[str]
    human_required: bool
    guardrail_hits: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    failed_safe: bool = False


Disposition = Literal[
    "accepted_gate",
    "accepted_verified",
    "accepted_unverified",
    "discarded_gate",
    "discarded_verifier",
    "dropped_unclear",
    "deduped",
    "overflow",
]


class FindingRecord(BaseModel):
    finding: Finding
    gate: GateResult | None = None
    gate_score: float | None = None
    severity: float | None = None
    """Effective severity (judge score after per-category caps); raw score is in `gate`."""
    disposition: Disposition
    rank: int | None = None
    reason: str = ""


class VerifierResult(BaseModel):
    verdict: Literal["confirmed", "refuted", "unclear"]
    reasoning: str
    evidence: str = ""


class ReviewResult(BaseModel):
    pr_id: str
    pipeline: str
    route: RouteDecision
    inline: list[FindingRecord] = Field(default_factory=list)
    overflow: list[FindingRecord] = Field(default_factory=list)
    discarded: list[FindingRecord] = Field(default_factory=list)
    summary: str = ""
    errors: list[str] = Field(default_factory=list)
    """Fatal: the review could not be completed (nothing trustworthy to report)."""
    warnings: list[str] = Field(default_factory=list)
    """Partial failures: some agents/judge calls failed; results may be incomplete."""


ReviewContext.model_rebuild()
