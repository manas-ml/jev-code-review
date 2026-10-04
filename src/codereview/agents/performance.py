from codereview.agents.base import AgentSpec

SPEC = AgentSpec(
    name="performance",
    role="You find performance regressions introduced by this change.",
    focus=(
        "N+1 queries, unbounded loops or memory growth, blocking I/O in async paths, "
        "quadratic algorithms on user-sized data, missing indexes implied by new queries, "
        "repeated expensive work inside hot paths."
    ),
    do_not_flag=[
        "micro-optimizations outside hot paths",
        "premature caching suggestions",
        "performance claims without a concrete input size or call frequency",
    ],
)
