import httpx
import pytest
import respx

from codereview.context.builder import triage_state
from codereview.judges.base import JudgeError
from codereview.judges.jev.client import JevJudge
from codereview.models import Finding, GateState
from codereview.pipeline.run import run_pipeline
from codereview.tracing import Tracer
from conftest import load

URL = "https://openrouter.ai/api/alpha/decisions"


def noul(p):
    return {"type": "noul", "noul": p}


def score(x, conf=0.95):
    return {"type": "score", "score": x, "confidence": conf, "probabilities": {}, "legend": {}}


TRIAGE_OK = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "change_type": {
            "type": "choice",
            "choice": "bugfix",
            "confidence": 0.9,
            "probabilities": {},
        },
        "risk": score(2.4),
        "trivial": noul(0.1),
        "touches_sensitive": noul(0.2),
        "changes_public_contract": noul(0.1),
        "lacks_test_coverage": noul(0.8),
        "perf_sensitive": noul(0.05),
    },
    "usage": {"input_tokens": 300, "output_tokens": 0, "cost": 0.00003},
}
GATE_OK = {
    "answers": {
        "is_real_defect": noul(0.9),
        "severity": score(3.1),
        "actionable": noul(0.8),
        "duplicate": noul(0.02),
    },
    "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.00001},
}


def judge(settings, retries=1):
    return JevJudge(settings, httpx.AsyncClient(), max_retries=retries, backoff_base=0)


@respx.mock
async def test_triage_request_shape_and_parsing(settings):
    route = respx.post(URL).respond(json=TRIAGE_OK)
    t = Tracer("jev", "p")
    res = await judge(settings).triage(triage_state(load("bugfix"), 120_000), t)
    body = route.calls[0].request.read()
    import json

    req = json.loads(body)
    assert req["model"] == "typesafe/jev-1.13"
    assert set(req["questions"]) == {
        "change_type",
        "risk",
        "trivial",
        "touches_sensitive",
        "changes_public_contract",
        "lacks_test_coverage",
        "perf_sensitive",
    }
    assert req["questions"]["risk"]["criteria"] == [
        "trivial",
        "low",
        "moderate",
        "high",
        "critical",
    ]
    assert "ignore" in req["questions"]["trivial"]["instructions"].lower()
    assert req["questions"]["change_type"]["criteria"]["dependency_bump"]
    assert res.change_type == "bugfix" and res.risk.value == 2.4
    assert res.lacks_test_coverage.value == 0.8
    assert t.calls[0].kind == "jev" and t.calls[0].answers is not None and t.total_cost > 0


@respx.mock
async def test_gate_parsing(settings):
    respx.post(URL).respond(json=GATE_OK)
    f = Finding(
        id="1", agent="a", file="f", line=1, title="t", explanation="e", evidence="x", category="c"
    )
    g = await judge(settings).gate(GateState(finding=f, code_window="x", pr_title="p"))
    assert g.is_real_defect.value == 0.9 and g.severity.value == 3.1


@respx.mock
async def test_retries_then_succeeds_on_429(settings):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=TRIAGE_OK)]
    )
    await judge(settings).triage(triage_state(load("bugfix"), 1000))
    assert route.call_count == 2


@respx.mock
async def test_persistent_500_raises_judge_error(settings):
    respx.post(URL).respond(500)
    with pytest.raises(JudgeError):
        await judge(settings, retries=2).triage(triage_state(load("bugfix"), 1000))


@respx.mock
async def test_malformed_answers_raise(settings):
    respx.post(URL).respond(json={"answers": {"risk": score(1)}})
    with pytest.raises(JudgeError):
        await judge(settings).triage(triage_state(load("bugfix"), 1000))


@respx.mock
async def test_simulated_jev_500_gives_full_deep_review(settings):
    """Acceptance criterion: Jev 500 -> full deep review, not a skip."""
    from conftest import FakeLLM, default_handler

    respx.post(URL).respond(500)
    res = await run_pipeline(
        "jev",
        load("docs_only"),
        FakeLLM(default_handler),
        judge(settings, 1),
        settings,
        Tracer("jev", "d"),
    )
    assert not res.route.skip and res.route.depth == "deep" and len(res.route.agents) == 5
