"""Pick the judge implementation for a pipeline."""

from __future__ import annotations

import httpx

from codereview.config import Settings
from codereview.judges.base import Judge
from codereview.judges.claude_judge import LLMJudge
from codereview.judges.mock_judge import MockJevJudge
from codereview.llm.client import LLM


def make_judge(
    pipeline: str, settings: Settings, llm: LLM, http: httpx.AsyncClient | None = None
) -> Judge:
    if pipeline == "baseline":
        return LLMJudge(llm, settings)
    if pipeline == "jev":
        if settings.jev_mode == "mock":
            return MockJevJudge()
        from codereview.judges.jev.client import JevJudge

        return JevJudge(settings, http)
    raise ValueError(f"unknown pipeline {pipeline}")
