from codereview.agents.base import AgentSpec

SPEC = AgentSpec(
    name="correctness",
    role="You find functional bugs introduced by this change.",
    focus=(
        "Logic errors, wrong operators or units, off-by-one, null/None handling, unhandled "
        "error paths, broken invariants, race conditions, resource leaks, incorrect API usage, "
        "and behavior that contradicts the PR's stated intent. Trace the changed code path; "
        "use tools to read callers when the diff alone is ambiguous."
    ),
    do_not_flag=[
        "hypothetical inputs that no caller can produce",
        "code you have not read in full when the claim depends on it",
        "refactors that preserve behavior",
    ],
)
