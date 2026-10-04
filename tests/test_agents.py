import pytest

from codereview.agents import REGISTRY
from codereview.agents.base import run_agent
from codereview.agents.tools import RepoTools, SandboxError
from codereview.tracing import Tracer
from conftest import FakeLLM, finding_json, load


async def test_invalid_json_retries_once_then_succeeds(settings):
    outputs = iter(["not json", finding_json()])
    llm = FakeLLM(lambda s, m: next(outputs))
    ctx = load("bugfix")
    out = await run_agent(REGISTRY["correctness"], ctx, ctx.files, llm, settings)
    assert len(out) == 1 and out[0].agent == "correctness" and len(llm.calls) == 2
    assert "failed validation" in llm.calls[1][1][-1]["content"]


async def test_invalid_twice_drops_and_logs(settings):
    llm = FakeLLM(lambda s, m: '{"findings": [{"file": 1}]}')
    ctx = load("bugfix")
    t = Tracer("x", "y")
    assert await run_agent(REGISTRY["security"], ctx, ctx.files, llm, settings, t) == []
    assert t.extra["dropped_agents"][0]["agent"] == "security"


async def test_cap_on_findings(settings):
    import json

    many = json.dumps({"findings": json.loads(finding_json())["findings"] * 12})
    llm = FakeLLM(lambda s, m: many)
    ctx = load("bugfix")
    out = await run_agent(REGISTRY["correctness"], ctx, ctx.files, llm, settings)
    assert len(out) == settings.max_findings_per_agent


def test_every_agent_has_do_not_flag_and_injection_rule():
    for spec in REGISTRY.values():
        p = spec.system_prompt(8)
        assert "Additionally do NOT flag" in p and spec.do_not_flag
        assert "untrusted data" in p and "At most" not in p and "at most 8" in p


def test_tools_reject_traversal_and_are_readonly(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    (tmp_path.parent / "secret.txt").write_text("nope")
    t = RepoTools(tmp_path)
    assert "1: hello" in t.read_file("a.txt")
    with pytest.raises(SandboxError):
        t.read_file("../secret.txt")
    assert t.call("read_file", {"path": "../secret.txt"}).startswith("error")
    assert t.call("list_dir", {"path": "/etc"}).startswith("error")
    assert t.call("write_file", {"path": "x"}).startswith("error: unknown tool")
    assert "a.txt:1: hello" in t.grep("hello")


async def test_tool_loop_executes_read_only_tools(settings, tmp_path):
    (tmp_path / "f.py").write_text("x = 1\n")
    state = {"n": 0}

    def tool_call(content, calls):
        from codereview.llm.client import LLMResponse

        return LLMResponse(content=content, tool_calls=calls)

    class ToolLLM(FakeLLM):
        async def chat(self, messages, **kw):
            state["n"] += 1
            if state["n"] == 1:
                return tool_call(
                    "",
                    [
                        {
                            "id": "1",
                            "function": {"name": "read_file", "arguments": '{"path": "f.py"}'},
                        }
                    ],
                )
            assert messages[-1]["role"] == "tool" and "1: x = 1" in messages[-1]["content"]
            return tool_call('{"findings": []}', [])

    ctx = load("bugfix")
    ctx.repo_root = str(tmp_path)
    out = await run_agent(
        REGISTRY["correctness"], ctx, ctx.files, ToolLLM(lambda s, m: ""), settings
    )
    assert out == [] and state["n"] == 2
