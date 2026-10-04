from codereview.agents.base import AgentSpec

SPEC = AgentSpec(
    name="tests",
    role="You assess whether behavior changes are adequately tested.",
    focus=(
        "Behavior changes or new branches with no corresponding test change, tests that no "
        "longer assert the changed behavior, tests that would pass regardless of the fix, "
        "and removed or weakened assertions. Name the specific untested behavior."
    ),
    do_not_flag=[
        "requests for 100% coverage",
        "missing tests for trivial getters, config, or docs",
        "test style or naming preferences",
    ],
)
