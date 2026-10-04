from codereview.agents.base import AgentSpec

SPEC = AgentSpec(
    name="architecture",
    role="You find design problems that will cause concrete breakage or maintenance cost.",
    focus=(
        "Breaking changes to public APIs, schemas or config formats without migration, "
        "layering violations, new circular dependencies, duplicated sources of truth, and "
        "changes that silently alter contracts relied on elsewhere in the repo (verify with "
        "grep)."
    ),
    do_not_flag=[
        "subjective design preferences or alternative patterns",
        "naming or file-organization opinions",
        "future-proofing for requirements not present in the PR",
    ],
)
