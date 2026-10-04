import pytest

from codereview.config import GateThresholds, RoutingThresholds
from codereview.models import GateResult, Scored, TriageResult
from codereview.pipeline.routing import decide_gate, route

TH = RoutingThresholds()
PATTERNS = [r"(^|/)auth", r"^\.github/workflows/"]


def tri(
    risk=1.0, trivial=0.1, sens=0.05, contract=0.1, tests=0.1, perf=0.1, conf=0.9
) -> TriageResult:
    def s(v: float) -> Scored:
        return Scored(value=v, confidence=conf)

    return TriageResult(
        change_type="feature",
        change_type_confidence=conf,
        risk=s(risk),
        trivial=s(trivial),
        touches_sensitive=s(sens),
        changes_public_contract=s(contract),
        lacks_test_coverage=s(tests),
        perf_sensitive=s(perf),
    )


def r(t, paths=("a.py",)):
    return route(t, list(paths), TH, PATTERNS)


@pytest.mark.parametrize(
    "trivial,risk,sens,skip",
    [
        (0.91, 0.49, 0.09, True),
        (0.90, 0.49, 0.09, False),  # trivial must be strictly > 0.9
        (0.91, 0.50, 0.09, False),  # risk must be < 0.5
        (0.91, 0.49, 0.10, False),  # sens must be < 0.1
    ],
)
def test_skip_boundaries(trivial, risk, sens, skip):
    assert r(tri(risk=risk, trivial=trivial, sens=sens)).skip is skip


def test_guardrail_blocks_skip_and_forces_deep_human():
    d = r(tri(risk=0.0, trivial=0.99, sens=0.0), ["app/auth/x.py"])
    assert not d.skip and d.depth == "deep" and d.human_required
    assert "security" in d.agents and d.guardrail_hits == ["app/auth/x.py"]


def test_workflow_guardrail():
    d = r(tri(), [".github/workflows/ci.yml"])
    assert d.depth == "deep" and d.human_required


@pytest.mark.parametrize(
    "kw,agent,present",
    [
        ({"sens": 0.31}, "security", True),
        ({"sens": 0.30}, "security", False),
        ({"tests": 0.51}, "tests", True),
        ({"tests": 0.50}, "tests", False),
        ({"contract": 0.51}, "architecture", True),
        ({"contract": 0.50}, "architecture", False),
        ({"perf": 0.51}, "performance", True),
        ({"perf": 0.50}, "performance", False),
    ],
)
def test_agent_selection_boundaries(kw, agent, present):
    d = r(tri(**kw))
    assert ("correctness" in d.agents) and ((agent in d.agents) is present)


@pytest.mark.parametrize(
    "risk,depth",
    [(1.99, "light"), (2.0, "deep")],
)
def test_deep_risk_boundary(risk, depth):
    assert r(tri(risk=risk)).depth == depth


def test_deep_when_more_than_two_agents():
    assert r(tri(tests=0.6, contract=0.6)).depth == "deep"  # 3 agents
    assert r(tri(tests=0.6)).depth == "light"  # 2 agents


@pytest.mark.parametrize(
    "risk,sens,human",
    [(3.19, 0.5, False), (3.2, 0.5, True), (1.0, 0.85, False), (1.0, 0.86, True)],
)
def test_human_boundaries(risk, sens, human):
    assert r(tri(risk=risk, sens=sens)).human_required is human


def test_failure_fails_safe_to_full_deep():
    d = route(None, ["a.py"], TH, PATTERNS, failure="HTTP 500")
    assert not d.skip and d.depth == "deep" and len(d.agents) == 5 and d.failed_safe


def test_low_confidence_fails_safe_even_if_trivial():
    d = r(tri(risk=0.0, trivial=0.99, sens=0.0, conf=0.4))
    assert not d.skip and d.failed_safe and d.depth == "deep"


def test_threshold_change_changes_behavior():
    t = tri(tests=0.6)
    assert "tests" in route(t, ["a.py"], TH, PATTERNS).agents
    strict = RoutingThresholds(tests_p=0.7)
    assert "tests" not in route(t, ["a.py"], strict, PATTERNS).agents


G = GateThresholds()


def gate(real=0.9, sev=3.0, act=0.9, dup=0.0) -> GateResult:
    def s(v: float) -> Scored:
        return Scored(value=v)

    return GateResult(is_real_defect=s(real), severity=s(sev), actionable=s(act), duplicate=s(dup))


@pytest.mark.parametrize(
    "kw,action",
    [
        ({"dup": 0.71}, "discard"),
        ({"dup": 0.70}, "accept"),
        ({"real": 0.34}, "discard"),
        ({"real": 0.35, "act": 0.9}, "verify"),
        ({"act": 0.29}, "discard"),
        ({"act": 0.30}, "verify"),
        ({"real": 0.8, "act": 0.6, "sev": 2.0}, "accept"),
        ({"real": 0.79}, "verify"),
        ({"act": 0.59}, "verify"),
        ({"sev": 1.99}, "verify"),
    ],
)
def test_gate_boundaries(kw, action):
    assert decide_gate(gate(**kw), G)[0] == action
