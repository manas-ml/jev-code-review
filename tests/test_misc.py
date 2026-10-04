import json

import httpx
import respx

from codereview.config import Pricing
from codereview.llm.client import OpenRouterClient, chat_validated
from codereview.models import Finding, FindingRecord, GateResult, Scored
from codereview.pipeline.dedupe import cluster, pick_representative
from codereview.pipeline.rank import rank_and_cap
from codereview.tracing import Tracer, redact
from conftest import FakeLLM

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
    from pydantic import BaseModel

    class _Answers(BaseModel):
        answers: dict[str, int]

    outs = iter(['{"answers": 1}', '{"answers": {}}'])

    llm = FakeLLM(lambda s, m: next(outs))
    res = await chat_validated(
        llm, _Answers, [{"role": "user", "content": "q"}], model="m", stage="s"
    )
    assert res.answers == {}


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
    return FindingRecord(
        finding=f, gate=g, gate_score=score, severity=sev, disposition="accepted_gate"
    )


def test_cluster_by_line_and_title_order_independent():
    a = _rec("a", 10, "Null pointer dereference in handler").finding
    b = _rec("b", 12, "Possible null pointer dereference").finding
    c = _rec("c", 30, "Null pointer dereference in handler").finding  # too far
    d = _rec("d", 11, "Unrelated sql injection").finding
    groups = cluster([a, b, c, d], 3)
    assert sorted(sorted(f.id for f in g) for g in groups) == [["a", "b"], ["c"], ["d"]]
    assert sorted(sorted(f.id for f in g) for g in cluster([d, c, b, a], 3)) == [
        ["a", "b"],
        ["c"],
        ["d"],
    ]


def test_representative_prefers_grounded_evidence_then_agent():
    base = _rec("x", 10, "t").finding
    ungrounded = base.model_copy(update={"id": "u", "agent": "security", "evidence": "nope zz"})
    grounded = base.model_copy(update={"id": "g", "agent": "tests", "evidence": "x = 1"})
    assert pick_representative([ungrounded, grounded], lambda f: "x = 1\n").id == "g"
    sec = base.model_copy(update={"id": "s", "agent": "security"})
    cor = base.model_copy(update={"id": "c", "agent": "correctness"})
    assert pick_representative([cor, sec], lambda f: "").id == "s"


def test_rank_orders_by_severity_then_score_and_caps():
    recs = [
        _rec("low", 1, "x", sev=1, score=0.99),
        _rec("hi", 2, "y", sev=4, score=0.5),
        _rec("mid", 3, "z", sev=4, score=0.9),
    ]
    inline, overflow = rank_and_cap(recs, 2)
    assert [r.finding.id for r in inline] == ["mid", "hi"]
    assert overflow[0].finding.id == "low" and overflow[0].disposition == "overflow"


def test_summary_report_has_ranking_table_and_severity_counts():
    from codereview.models import ReviewResult, RouteDecision
    from codereview.pipeline.output import render_markdown

    recs = [_rec("low", 1, "minor thing", sev=1, score=0.9), _rec("hi", 2, "big bug", sev=3.6)]
    inline, overflow = rank_and_cap(recs, 10)
    route = RouteDecision(skip=False, depth="deep", agents=["correctness"], human_required=True)
    md = render_markdown(ReviewResult(pr_id="p", pipeline="jev", route=route, inline=inline))
    assert "## Ranking" in md and "Human review required: **yes**" in md
    assert "1 critical" in md and "1 minor" in md
    rows = [ln for ln in md.splitlines() if ln.startswith("| 1 |") or ln.startswith("| 2 |")]
    assert "big bug" in rows[0] and "minor thing" in rows[1]


def _result_with(recs):
    from codereview.models import ReviewResult, RouteDecision

    inline, overflow = rank_and_cap(recs, 10)
    route = RouteDecision(skip=False, depth="deep", agents=["correctness"], human_required=False)
    return ReviewResult(pr_id="p", pipeline="jev", route=route, inline=inline, overflow=overflow)


def test_commentable_lines_from_hunks():
    from codereview.github.client import commentable_lines
    from codereview.models import FileDiff

    patch = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +10,4 @@\n a\n-b\n+c\n+d\n e\n"
    assert commentable_lines([FileDiff(path="f", patch=patch)]) == {"f": {10, 11, 12, 13}}


def test_only_high_severity_posted_inline_rest_in_body():
    from codereview.pipeline.output import render_review_body, split_for_posting

    recs = [
        _rec("hi", 10, "serious bug", sev=3.6),
        _rec("mid", 11, "moderate thing", sev=2.0),
        _rec("off", 99, "serious but outside diff", sev=3.9),
    ]
    res = _result_with(recs)
    visible = {"f.py": {10, 11}}
    comments, rest = split_for_posting(res, 3.0, visible, 10)
    assert [c["line"] for c in comments] == [10]
    assert {r.finding.id for r in rest} == {"mid", "off"}
    body = render_review_body(res, len(comments), rest, 3.0)
    assert "Findings not posted inline" in body and "moderate thing" in body
    assert "serious but outside diff" in body  # never lost
    assert "severity >= 3" in body


def test_lower_floor_posts_more():
    from codereview.pipeline.output import split_for_posting

    res = _result_with([_rec("a", 1, "x", sev=3.6), _rec("b", 2, "y", sev=2.0)])
    assert len(split_for_posting(res, 3.0, None, 10)[0]) == 1
    assert len(split_for_posting(res, 2.0, None, 10)[0]) == 2
    assert len(split_for_posting(res, 0.0, None, 1)[0]) == 1  # cap respected


@respx.mock
async def test_post_review_falls_back_to_body_on_422():
    from codereview.github.client import post_review

    url = "https://api.github.com/repos/o/r/pulls/1/reviews"
    route = respx.post(url).mock(side_effect=[httpx.Response(422), httpx.Response(200, json={})])
    ok = await post_review(
        "https://github.com/o/r/pull/1", "t", "body", [{"path": "f", "line": 1}], "fallback"
    )
    assert ok is False and route.call_count == 2
    import json as _j

    assert _j.loads(route.calls[1].request.read()) == {"body": "fallback", "event": "COMMENT"}


def test_review_json_stays_valid_when_redacting(tmp_path):
    from codereview.pipeline.output import write_outputs

    rec = _rec("a", 1, "t")
    rec.finding.suggested_fix = 'secret = os.environ["SESSION_SECRET"]  # api_key="abcdefghijkl"'
    res = _result_with([rec])
    write_outputs(tmp_path, res, Tracer("jev", "p"))
    data = json.loads((tmp_path / "review.json").read_text())  # must parse
    assert "abcdefghijkl" not in json.dumps(data)
