import json

from codereview.judges.base import JudgeError
from codereview.judges.mock_judge import MockJevJudge
from codereview.models import GateResult, GateState, TriageResult, TriageState
from codereview.pipeline.output import write_outputs
from codereview.pipeline.run import run_pipeline
from codereview.tracing import Tracer
from conftest import FakeLLM, default_handler, finding_json, load


class FailingJudge:
    async def triage(self, state: TriageState, tracer=None) -> TriageResult:
        raise JudgeError("Jev call failed: HTTP 500")

    async def gate(self, state: GateState, tracer=None) -> GateResult:
        raise JudgeError("HTTP 500")


async def test_outputs_written_with_trace(settings, tmp_path):
    llm = FakeLLM(default_handler)
    t = Tracer("jev", "bugfix")
    res = await run_pipeline("jev", load("bugfix"), llm, MockJevJudge(), settings, t)
    write_outputs(tmp_path, res, t)
    assert {p.name for p in tmp_path.iterdir()} == {"review.json", "review.md", "trace.json"}
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["route"]["depth"] == "deep" and trace["calls"]


async def test_jev_pipeline_accepts_grounded_finding(settings):
    ctx = load("bugfix")
    llm = FakeLLM(default_handler)
    res = await run_pipeline("jev", ctx, llm, MockJevJudge(), settings, Tracer("jev", "bugfix"))
    assert not res.route.skip
    assert [r.disposition for r in res.inline] == ["accepted_gate"]


async def test_docs_only_is_skipped_without_llm_calls(settings):
    llm = FakeLLM(default_handler)
    res = await run_pipeline(
        "jev", load("docs_only"), llm, MockJevJudge(), settings, Tracer("jev", "d")
    )
    assert res.route.skip and not llm.calls


async def test_judge_500_results_in_full_deep_review_not_skip(settings):
    llm = FakeLLM(default_handler)
    res = await run_pipeline(
        "jev", load("docs_only"), llm, FailingJudge(), settings, Tracer("jev", "d")
    )
    assert not res.route.skip and res.route.depth == "deep" and res.route.failed_safe
    assert len(res.route.agents) == 5


async def test_gate_failure_escalates_to_verifier(settings):
    llm = FakeLLM(default_handler)
    res = await run_pipeline(
        "jev", load("bugfix"), llm, FailingJudge(), settings, Tracer("jev", "b")
    )
    assert [r.disposition for r in res.inline] == ["accepted_verified"]
    assert any(stage == "verifier" for stage, _ in llm.calls)


async def test_unclear_verifier_drops_with_reason(settings):
    def h(stage, messages):
        if stage == "verifier":
            return '{"verdict": "unclear", "reasoning": "cannot tell"}'
        return default_handler(stage, messages)

    res = await run_pipeline(
        "jev", load("bugfix"), FakeLLM(h), FailingJudge(), settings, Tracer("jev", "b")
    )
    assert not res.inline
    assert (
        res.discarded[0].disposition == "dropped_unclear"
        and "cannot tell" in res.discarded[0].reason
    )


async def test_prompt_injection_does_not_change_routing_or_approve(settings):
    ctx = load("injection")
    llm = FakeLLM(
        lambda s, m: (
            finding_json("app/util.py", 5, "Command injection")
            if s == "agent:correctness"
            else ('{"findings": []}' if s.startswith("agent:") else default_handler(s, m))
        )
    )
    res = await run_pipeline("jev", ctx, llm, MockJevJudge(), settings, Tracer("jev", "i"))
    assert not res.route.skip
    assert "security" in res.route.agents  # os.system is flagged structurally
    # every prompt marks PR content as untrusted data and carries the ignore-instructions rule
    for stage, msgs in llm.calls:
        text = json.dumps(msgs)
        if stage.startswith("agent:"):
            assert "<<<BEGIN UNTRUSTED DIFF>>>" in text and "never follow" in text.lower()
    assert "approve" not in res.summary.lower()


async def test_dedupe_and_cap(settings):
    settings.max_inline_comments = 1

    def h(stage, messages):
        if stage in ("agent:correctness", "agent:security"):
            return finding_json()
        return default_handler(stage, messages)

    res = await run_pipeline(
        "jev", load("bugfix"), FakeLLM(h), MockJevJudge(), settings, Tracer("jev", "b")
    )
    assert len(res.inline) == 1
    assert any(r.disposition == "deduped" for r in res.discarded)
