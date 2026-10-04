"""Pick the judge implementation."""

from __future__ import annotations

import httpx

from codereview.config import Settings
from codereview.judges.base import Judge
from codereview.judges.mock_judge import MockJevJudge
from codereview.llm.client import LLM


def make_judge(
    pipeline: str, settings: Settings, llm: LLM, http: httpx.AsyncClient | None = None
) -> Judge:
    if pipeline != "jev":
        raise ValueError(f"unknown pipeline {pipeline}")
    if settings.jev_mode == "mock":
        return MockJevJudge()
    from codereview.judges.jev.client import JevJudge

    return JevJudge(settings, http)
