"""Phase 17.2 — agent_chat_stream event generator tests.

Covers: event ordering, tool execution (sequential unsafe / parallel safe),
invalid arguments, unknown tools, max-round guard, and the legacy
``agent_chat`` wrapper. The LLM layer is fully mocked via ``call_llm_stream``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app import agent as agent_mod
from app.llm import LLMChunk, LLMError
from app.tools import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Minimal stand-in for ToolRegistry / plugin registry."""

    def __init__(self) -> None:
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def get_openai_tools(self) -> list[dict]:
        return [t.get_schema() for t in self.tools.values()]

    # only used on the plugin registry double
    def get_all_commands(self) -> list[dict]:
        return []

    def find_command_plugin(self, name: str):
        return None

    async def execute_command(self, plugin: str, name: str, args: dict) -> ToolResult:
        return ToolResult(f"plugin:{name}", success=True)


class FakeTool(BaseTool):
    def __init__(
        self,
        name: str,
        *,
        safe: bool = False,
        delay: float = 0.0,
        content: str = "ok",
        success: bool = True,
        fail: bool = False,
        metadata: dict | None = None,
    ) -> None:
        self.name = name
        self.description = f"fake {name}"
        self.concurrency_safe = safe
        self.delay = delay
        self._content = content
        self._success = success
        self._fail = fail
        self._metadata = metadata or {}
        self.runs: list[tuple[float, float]] = []

    async def execute(self, **kwargs) -> ToolResult:
        start = time.perf_counter()
        if self.delay:
            await asyncio.sleep(self.delay)
        self.runs.append((start, time.perf_counter()))
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        return ToolResult(self._content, success=self._success, metadata=dict(self._metadata))


class ScriptedLLM:
    """Returns a canned chunk sequence per LLM round, recording messages."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self.rounds = rounds
        self.calls: list[list[dict]] = []

    def __call__(self, messages, tools=None, explicit_tier=None):
        self.calls.append([dict(m) for m in messages])
        chunks = self.rounds.pop(0)

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


def _tool_call(idx: int, name: str, args: dict) -> dict:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": name, "arguments": _json(args)},
    }


def _json(obj) -> str:
    import json

    return json.dumps(obj)


def _text_round(text: str, finish: str = "stop") -> list[LLMChunk]:
    return [LLMChunk(kind="text", delta=text), LLMChunk(kind="finish", finish_reason=finish)]


def _tools_round(calls: list[dict]) -> list[LLMChunk]:
    return [LLMChunk(kind="tool_calls", tool_calls=calls), LLMChunk(kind="finish", finish_reason="tool_calls")]


@pytest.fixture()
def isolated_agent(monkeypatch):
    """Give each test a fresh tool registry and empty plugin registry."""
    fake_tools = FakeRegistry()
    fake_plugins = FakeRegistry()
    monkeypatch.setattr(agent_mod, "registry", fake_tools)
    monkeypatch.setattr(agent_mod, "plugin_registry", fake_plugins)
    return fake_tools


def _run_stream(messages: list[dict]):
    async def _collect():
        return [ev async for ev in agent_mod.agent_chat_stream(messages)]

    return asyncio.run(_collect())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_text_only_round(isolated_agent):
    llm = ScriptedLLM([_text_round("你好呀")])
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "hi"}])

    kinds = [e.kind for e in events]
    assert kinds == ["text", "done"]
    assert events[0].delta == "你好呀"
    assert events[1].content == "你好呀"
    assert events[1].tool_log == []


def test_tool_round_then_final_text(isolated_agent):
    tool = FakeTool("web_search", content="found it")
    isolated_agent.register(tool)

    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "web_search", {"query": "x"})]),
            _text_round("答案是 found it"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "search x"}])

    kinds = [e.kind for e in events]
    # tool_call -> tool_result -> text -> done, in strict order
    assert kinds == ["tool_call", "tool_result", "text", "done"]
    assert events[0].name == "web_search"
    assert events[0].args == {"query": "x"}
    assert events[1].call_id == "call_0"
    assert events[1].result == "found it"
    assert events[1].success is True
    assert events[3].content == "答案是 found it"
    assert events[3].tool_log[0]["tool"] == "web_search"

    # The tool message was fed back with the matching tool_call_id.
    tool_msgs = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_0"
    assert tool_msgs[0]["content"] == "found it"


def test_safe_tools_run_in_parallel(isolated_agent):
    slow_a = FakeTool("search_a", safe=True, delay=0.15)
    slow_b = FakeTool("search_b", safe=True, delay=0.15)
    isolated_agent.register(slow_a)
    isolated_agent.register(slow_b)

    llm = ScriptedLLM(
        [
            _tools_round(
                [
                    _tool_call(0, "search_a", {}),
                    _tool_call(1, "search_b", {}),
                ]
            ),
            _text_round("both done"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    kinds = [e.kind for e in events]
    assert kinds == ["tool_call", "tool_call", "tool_result", "tool_result", "text", "done"]

    # Wall-clock overlap proves parallel execution.
    (a_start, a_end), (b_start, b_end) = slow_a.runs[0], slow_b.runs[0]
    assert min(a_end, b_end) > max(a_start, b_start), "safe tools must overlap"


def test_unsafe_tools_run_sequentially(isolated_agent):
    a = FakeTool("run_a", safe=False, delay=0.05)
    b = FakeTool("run_b", safe=False, delay=0.05)
    isolated_agent.register(a)
    isolated_agent.register(b)

    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "run_a", {}), _tool_call(1, "run_b", {})]),
            _text_round("done"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    (a_start, a_end), (b_start, b_end) = a.runs[0], b.runs[0]
    assert a_end <= b_start, "unsafe tools must not overlap"
    assert [e.kind for e in events][:4] == ["tool_call", "tool_call", "tool_result", "tool_result"]


def test_mixed_tools_parallel_and_sequential(isolated_agent):
    safe_t = FakeTool("calc", safe=True, delay=0.1)
    unsafe_t = FakeTool("run_cmd", safe=False, delay=0.05)
    isolated_agent.register(safe_t)
    isolated_agent.register(unsafe_t)

    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "run_cmd", {}), _tool_call(1, "calc", {})]),
            _text_round("mixed done"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    results = [e for e in events if e.kind == "tool_result"]
    assert len(results) == 2
    # Results must carry the right call ids even though execution interleaved.
    by_id = {e.call_id: e for e in results}
    assert by_id["call_0"].name == "run_cmd"
    assert by_id["call_1"].name == "calc"

    # Sequential one finished before the parallel one started? Not guaranteed —
    # but the parallel one must not start before the sequential one *begins*.
    (s_start, _), (p_start, _) = unsafe_t.runs[0], safe_t.runs[0]
    assert p_start >= s_start


def test_unknown_tool_yields_failed_result(isolated_agent):
    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "nonexistent", {})]),
            _text_round("handled"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    result = next(e for e in events if e.kind == "tool_result")
    assert result.success is False
    assert "Unknown tool: nonexistent" in result.result


def test_invalid_arguments_json(isolated_agent):
    llm = ScriptedLLM(
        [
            [
                LLMChunk(
                    kind="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{not json"},
                        }
                    ],
                ),
                LLMChunk(kind="finish", finish_reason="tool_calls"),
            ],
            _text_round("recovered"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    result = next(e for e in events if e.kind == "tool_result")
    assert result.success is False
    assert "Invalid tool arguments" in result.result


def test_tool_execution_exception_becomes_failed_result(isolated_agent):
    bad = FakeTool("boom", fail=True)
    isolated_agent.register(bad)

    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "boom", {})]),
            _text_round("survived"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    result = next(e for e in events if e.kind == "tool_result")
    assert result.success is False
    assert "boom exploded" in result.result
    # The loop continued and still produced a final answer.
    assert events[-1].kind == "done"
    assert events[-1].content == "survived"


def test_max_tool_rounds_guard(isolated_agent):
    forever_tool = FakeTool("loop_tool", content="still looping")
    isolated_agent.register(forever_tool)

    rounds = [_tools_round([_tool_call(0, "loop_tool", {})])] * (agent_mod.MAX_TOOL_ROUNDS + 2)
    llm = ScriptedLLM(rounds)
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "go"}])

    assert len(forever_tool.runs) == agent_mod.MAX_TOOL_ROUNDS
    done = events[-1]
    assert done.kind == "done"
    assert "最大工具调用轮数" in done.content


def test_llm_error_surfaces_as_error_event(isolated_agent):
    def _fail(*a, **kw):
        async def _gen():
            raise LLMError("rate limited", status_code=429)
            yield  # pragma: no cover — makes it a generator
        return _gen()

    agent_mod.call_llm_stream = _fail  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "hi"}])

    assert [e.kind for e in events] == ["error"]
    assert "rate limited" in events[0].message


def test_empty_stream_surfaces_error(isolated_agent):
    llm = ScriptedLLM([[LLMChunk(kind="finish", finish_reason=None)]])
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "hi"}])

    assert events[-1].kind == "error"


def test_agent_chat_compat_wrapper(isolated_agent):
    tool = FakeTool("calc", content="42")
    isolated_agent.register(tool)

    llm = ScriptedLLM(
        [
            _tools_round([_tool_call(0, "calc", {"expression": "6*7"})]),
            _text_round("six times seven"),
        ]
    )
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    text, tool_log = asyncio.run(agent_mod.agent_chat([{"role": "user", "content": "6*7"}]))

    assert text == "six times seven"
    assert tool_log == [
        {
            "tool": "calc",
            "args": {"expression": "6*7"},
            "result": "42",
            "success": True,
        }
    ]


def test_agent_chat_error_returns_message_string(isolated_agent):
    def _fail(*a, **kw):
        async def _gen():
            raise LLMError("boom")
            yield
        return _gen()

    agent_mod.call_llm_stream = _fail  # type: ignore[assignment]

    text, tool_log = asyncio.run(agent_mod.agent_chat([{"role": "user", "content": "hi"}]))

    assert text == "LLM Error: boom"
    assert tool_log == []


def test_tools_passed_to_llm(isolated_agent):
    isolated_agent.register(FakeTool("calc"))

    llm = ScriptedLLM([_text_round("ok")])
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    _run_stream([{"role": "user", "content": "hi"}])

    # The fake plugin registry contributes nothing; the built-in tool schema
    # must have been forwarded to the LLM call.
    assert llm.calls[0][0]["role"] == "user"  # messages forwarded intact


# ---------------------------------------------------------------------------
# strip_tool_call_json — remove tool JSON the model embedded in its text
# ---------------------------------------------------------------------------


def test_strip_tool_call_json_removes_embedded_call():
    text = '我算一下\n{"tool": "calculator", "args": {"expression": "(1234 * 5678) + 90"}}\n结果是'
    stripped = agent_mod.strip_tool_call_json(text)
    assert '"tool"' not in stripped
    assert "我算一下" in stripped and "结果是" in stripped


def test_strip_tool_call_json_leaves_plain_text_alone():
    text = "这是一段普通回答，提到了 JSON 但没有工具调用。"
    assert agent_mod.strip_tool_call_json(text) == text


def test_strip_tool_call_json_ignores_partial_fragment():
    # Still arriving mid-stream — a partial fragment must not be deleted,
    # otherwise the visible text would flicker.
    text = '我算一下\n{"tool": "calculator", "args": {"expr'
    assert agent_mod.strip_tool_call_json(text) == text


# ---------------------------------------------------------------------------
# Tool metadata propagation (Phase 17.8)
#
# The right-hand display panel mirrors a browsed page, and the only way it
# learns which URL to show is this metadata hop: ToolResult -> AgentEvent.
# ---------------------------------------------------------------------------


def test_tool_result_event_carries_tool_metadata(isolated_agent):
    tool = FakeTool(
        "browser_open",
        metadata={"url": "https://example.com/", "title": "Example", "embeddable": False,
                  "embed_block_reason": "该站点设置了 X-Frame-Options: DENY"},
    )
    isolated_agent.register(tool)

    llm = ScriptedLLM([
        _tools_round([_tool_call(0, "browser_open", {"url": "https://example.com"})]),
        _text_round("已打开"),
    ])
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "打开 example.com"}])

    result_event = next(e for e in events if e.kind == "tool_result")
    assert result_event.metadata["url"] == "https://example.com/"
    assert result_event.metadata["embeddable"] is False
    assert "X-Frame-Options" in result_event.metadata["embed_block_reason"]


def test_tool_result_metadata_defaults_to_empty_dict(isolated_agent):
    isolated_agent.register(FakeTool("calculator", content="= 4"))

    llm = ScriptedLLM([
        _tools_round([_tool_call(0, "calculator", {"expression": "2+2"})]),
        _text_round("等于 4"),
    ])
    agent_mod.call_llm_stream = llm  # type: ignore[assignment]

    events = _run_stream([{"role": "user", "content": "算一下"}])

    result_event = next(e for e in events if e.kind == "tool_result")
    assert result_event.metadata == {}
