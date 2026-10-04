from codereview.agents.base import AgentSpec

SPEC = AgentSpec(
    name="security",
    role="You find security vulnerabilities introduced by this change.",
    focus=(
        "Injection (SQL, command, template, path traversal), authn/authz bypass, timing-unsafe "
        "secret comparison, hardcoded secrets, unsafe deserialization, SSRF, weak crypto, "
        "disclosure of sensitive data in logs or responses. Show the concrete tainted path "
        "from untrusted input to the sink."
    ),
    do_not_flag=[
        "generic 'validate input' advice without a concrete exploitable path",
        "missing rate limiting or hardening unrelated to the diff",
        "dependency CVEs you cannot confirm from the diff or tool output",
    ],
)
