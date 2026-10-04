from codereview.agents import architecture, correctness, performance, security
from codereview.agents import tests as tests_agent
from codereview.agents.base import AgentSpec

REGISTRY: dict[str, AgentSpec] = {
    s.name: s
    for s in (
        correctness.SPEC,
        security.SPEC,
        tests_agent.SPEC,
        architecture.SPEC,
        performance.SPEC,
    )
}
