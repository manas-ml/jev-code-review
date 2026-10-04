import json

import httpx
import respx

from codereview.config import Pricing
from codereview.context.builder import triage_state
from codereview.judges.claude_judge import LLMJudge
from codereview.llm.client import OpenRouterClient, chat_validated
from codereview.models import Finding, FindingRecord, GateResult, Scored
from codereview.pipeline.dedupe import dedupe
from codereview.pipeline.rank import rank_and_cap
from codereview.tracing import Tracer, redact
from conftest import FakeLLM, load

OR = "https://openrouter.ai/api/v1/chat/completions"


def completion(content, usage=None):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
    }


@respx.mock
async def test_llm_client_retries_and_costs_from_price_table(settings):
    settings.prices = {"m": Pricing(input=1.0, output=2.0)}
    route = respx.post(OR).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=completion("hi"))]
    )
    c = OpenRouterClient(settings, httpx.AsyncClient(), backoff_base=0)
    t = Tracer("x", "y")
    r = await c.chat([{"role": "user", "content": "q"}], model="m", stage="s", tracer=t)
    assert route.call_count == 2 and r.content == "hi"
    assert abs(t.total_cost - (100 * 1 + 50 * 2) / 1e6) < 1e-12
    assert route.calls[0].request.headers["authorization"] == "Bearer test"


@respx.mock
async def test_llm_client_prefers_reported_cost(settings):
    respx.post(OR).respond(
        json=completion("x", {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.5})
    )
    c = OpenRouterClient(settings, httpx.AsyncClient())
    r = await c.chat([], model="m", stage="s")
    assert r.cost_usd == 0.5


async def test_chat_validated_retries_with_error():
    outs = iter(['{"answers": 1}', '{"answers": {}}'])
    from codereview.judges.claude_judge import _Answers

    llm = FakeLLM(lambda s, m: next(outs))
    res = await chat_validated(
        llm, _Answers, [{"role": "user", "content": "q"}], model="m", stage="s"
    )
    assert res.answers == {}


async def test_llm_judge_parses_answers(settings):
    answers = {
        "change_type": {"answer": "bugfix", "confidence": 0.9},
        "risk": {"answer": 2, "confidence": 0.8},
        **{
            k: {"answer": 0.3, "confidence": 0.9}
            for k in (
                "trivial",
                "touches_sensitive",
                "changes_public_contract",
                "lacks_test_coverage",
                "perf_sensitive",
            )
        },
    }
    llm = FakeLLM(lambda s, m: json.dumps({"answers": answers}))
    res = await LLMJudge(llm, settings).triage(triage_state(load("bugfix"), 1000))
    assert res.risk.value == 2 and res.change_type == "bugfix"


def test_redaction():
    s = "key sk-abcdefghijklmnopqrstuv and ghp_" + "a" * 30 + ' api_key="supersecretvalue"'
    out = redact(s)
    assert "abcdefghijklmnop" not in out and "supersecretvalue" not in out and "ghp_a" not in out


def _rec(id, line, title, sev=3.0, score=0.9):
    f = Finding(
        id=id,
        agent="a",
        file="f.py",
        line=line,
        title=title,
        explanation="e",
        evidence="x",
        category="c",
    )
    g = GateResult(
        is_real_defect=Scored(value=score),
        severity=Scored(value=sev),
        actionable=Scored(value=0.9),
        duplicate=Scored(value=0),
    )
    return FindingRecord(finding=f, gate=g, gate_score=score, disposition="accepted_gate")


def test_dedupe_by_line_and_title():
    a = _rec("a", 10, "Null pointer dereference in handler")
    b = _rec("b", 12, "Possible null pointer dereference")
    c = _rec("c", 30, "Null pointer dereference in handler")  # too far
    d = _rec("d", 11, "Unrelated sql injection")
    kept, dropped = dedupe([a, b, c, d], 3)
    assert {r.finding.id for r in kept} == {"a", "c", "d"} and dropped[0].finding.id == "b"


def test_rank_orders_by_severity_then_score_and_caps():
    recs = [
        _rec("low", 1, "x", sev=1, score=0.99),
        _rec("hi", 2, "y", sev=4, score=0.5),
        _rec("mid", 3, "z", sev=4, score=0.9),
    ]
    inline, overflow = rank_and_cap(recs, 2)
    assert [r.finding.id for r in inline] == ["mid", "hi"]
    assert overflow[0].finding.id == "low" and overflow[0].disposition == "overflow"
