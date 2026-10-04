"""Configuration: model IDs, thresholds, price table. Everything tunable lives here."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RoutingThresholds(BaseModel):
    skip_trivial_p: float = 0.9
    skip_risk_max: float = 0.5
    skip_sensitive_p_max: float = 0.1
    security_sensitive_p: float = 0.3
    tests_p: float = 0.5
    architecture_p: float = 0.5
    performance_p: float = 0.5
    deep_risk_min: float = 2.0
    deep_agent_count_over: int = 2
    human_risk_min: float = 3.2
    human_sensitive_p: float = 0.85
    min_confidence: float = 0.5
    """Any triage answer below this confidence fails safe to a deep review."""


class GateThresholds(BaseModel):
    discard_duplicate_p: float = 0.7
    discard_real_p: float = 0.35
    discard_actionable_p: float = 0.3
    accept_real_p: float = 0.8
    accept_actionable_p: float = 0.6
    accept_severity_min: int = 2


class Pricing(BaseModel):
    """USD per million tokens."""

    input: float = 0.0
    output: float = 0.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    typesafe_api_key: str = ""
    github_token: str = ""

    jev_mode: Literal["real", "mock"] = "mock"
    jev_model: str = "typesafe/jev-1.13"
    agent_model: str = "deepseek/deepseek-v4.1-flash"
    verifier_model: str = "deepseek/deepseek-v4.1-flash"
    baseline_judge_model: str = "deepseek/deepseek-v4.1-flash"
    match_judge_model: str = "deepseek/deepseek-v4.1-flash"

    max_concurrency: int = 4
    triage_char_budget: int = 120_000
    max_findings_per_agent: int = 8
    max_inline_comments: int = 10
    post_min_severity: float = 3.0
    """Only findings with gate severity >= this get inline PR comments (0 cosmetic .. 4 critical;
    3 = serious). Lower-severity findings go in the review body, collapsed."""
    dedupe_line_window: int = 3
    match_line_window: int = 5

    routing: RoutingThresholds = Field(default_factory=RoutingThresholds)
    gate: GateThresholds = Field(default_factory=GateThresholds)
    # Placeholder prices (unverified); override per model. Zero means "use cost from response".
    prices: dict[str, Pricing] = Field(default_factory=dict)

    sensitive_path_patterns: list[str] = Field(
        default_factory=lambda: [
            r"(^|/)auth",
            r"(^|/)secrets?",
            r"(^|/)migrations?/",
            r"^\.github/workflows/",
        ]
    )


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
