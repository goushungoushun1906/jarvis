"""Tool framework & executor tests — 覆盖 test_agent_stream.py 没管的部分。

test_agent_stream.py 已经把 agent 循环（事件顺序、并行/串行、异常兜底）测得很全，
这里聚焦它没覆盖的：

* ``app.tools`` 的 ToolResult / ToolRegistry / schema 生成
* ``agent._execute_tool_call`` 在**真实** registry 上的行为（含插件兜底）
* ``agent._sanitize_tool_content``（base64 剥离 + 截断，直接关系 LLM 请求体积）
* ``agent._extract_tool_calls``（非原生 function calling 的文本回退路径）
"""

from __future__ import annotations

import pytest

from app import agent as agent_mod
from app.tools import BaseTool, ToolRegistry, ToolResult


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


def test_tool_result_defaults():
    r = ToolResult("done")
    assert r.content == "done"
    assert r.success is True
    assert r.metadata == {}


def test_tool_result_to_dict_shape():
    r = ToolResult("x", success=False, metadata={"k": 1})
    assert r.to_dict() == {"content": "x", "success": False, "metadata": {"k": 1}}


def test_tool_result_metadata_is_not_shared_between_instances():
    """回归：metadata 用 `or {}` 兜底，但不能变成类级共享的可变默认值。"""
    a = ToolResult("a")
    b = ToolResult("b")
    a.metadata["leak"] = True
    assert b.metadata == {}


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class _Dummy(BaseTool):
    name = "dummy"
    description = "does nothing"
    parameters_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult("dummy-ok")


def test_registry_register_get_list():
    reg = ToolRegistry()
    tool = _Dummy()
    reg.register(tool)
    assert reg.get("dummy") is tool
    assert reg.get("nope") is None
    assert reg.list_tools() == [tool]


def test_registry_duplicate_name_overwrites():
    """同名注册必须覆盖而不是并存——否则 schema 里会出现两个同名 function。"""
    reg = ToolRegistry()
    first, second = _Dummy(), _Dummy()
    reg.register(first)
    reg.register(second)
    assert reg.list_tools() == [second]
    assert len(reg.get_openai_tools()) == 1


def test_registry_get_openai_tools_shape():
    reg = ToolRegistry()
    reg.register(_Dummy())
    schema = reg.get_openai_tools()[0]
    assert schema == {
        "type": "function",
        "function": {
            "name": "dummy",
            "description": "does nothing",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_base_tool_default_flags_are_conservative():
    """默认必须不可并发、不需审批——宁可慢，不可越权。"""

    class _Bare(BaseTool):
        name = "bare"
        description = ""
        parameters_schema = {}

        async def execute(self, **kwargs) -> ToolResult:
            return ToolResult("")

    t = _Bare()
    assert t.concurrency_safe is False
    assert t.requires_approval is False
    assert agent_mod._tool_is_safe("bare") is False


# ---------------------------------------------------------------------------
# _tool_is_safe  /  _execute_tool_call（真实 registry）
# ---------------------------------------------------------------------------


class _Safe(BaseTool):
    name = "safe_probe"
    concurrency_safe = True

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult("safe", metadata={"echo": kwargs})


class _Boom(BaseTool):
    name = "boom_probe"

    async def execute(self, **kwargs) -> ToolResult:
        raise RuntimeError("kaboom")


class _Slow(BaseTool):
    name = "slow_probe"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult("slow")


class _Strict(BaseTool):
    """只接受 schema 里声明过的参数——用来验证模型编造参数时不会炸链路。"""

    name = "strict_probe"
    parameters_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }

    async def execute(self, value: str = "") -> ToolResult:
        return ToolResult(f"strict:{value}")


@pytest.fixture
def real_registry():
    """往全局 registry 里塞测试工具，用完按原名还原。"""
    reg = agent_mod.registry
    saved = dict(reg._tools)
    for t in (_Safe(), _Boom(), _Slow(), _Dummy(), _Strict()):
        reg.register(t)
    yield reg
    reg._tools.clear()
    reg._tools.update(saved)


def test_tool_is_safe_reflects_flag(real_registry):
    assert agent_mod._tool_is_safe("safe_probe") is True
    assert agent_mod._tool_is_safe("slow_probe") is False
    assert agent_mod._tool_is_safe("totally_unknown") is False


@pytest.mark.anyio
async def test_execute_builtin_tool_returns_result(real_registry):
    result = await agent_mod._execute_tool_call("dummy", {})
    assert result.success is True
    assert result.content == "dummy-ok"


@pytest.mark.anyio
async def test_execute_passes_kwargs_through(real_registry):
    result = await agent_mod._execute_tool_call("safe_probe", {"a": 1, "b": "x"})
    assert result.metadata == {"echo": {"a": 1, "b": "x"}}


@pytest.mark.anyio
async def test_execute_swallows_exception_as_failed_result(real_registry):
    """工具抛异常不能炸掉整个 agent 循环，要变成 success=False 的结果。"""
    result = await agent_mod._execute_tool_call("boom_probe", {})
    assert result.success is False
    assert "kaboom" in result.content
    assert result.content.startswith("Tool error:")


@pytest.mark.anyio
async def test_execute_unknown_tool_reports_unknown(real_registry):
    result = await agent_mod._execute_tool_call("no_such_tool", {})
    assert result.success is False
    assert result.content == "Unknown tool: no_such_tool"


@pytest.mark.anyio
async def test_execute_unexpected_kwarg_becomes_failed_result(real_registry):
    """模型编造了 schema 里没有的参数 → TypeError 也要被吞成失败结果。"""
    result = await agent_mod._execute_tool_call("strict_probe", {"invented": 1})
    assert result.success is False
    assert result.content.startswith("Tool error:")


# ---------------------------------------------------------------------------
# _sanitize_tool_content — 直接决定送进 LLM 的体积
# ---------------------------------------------------------------------------


def test_sanitize_strips_base64_image_data():
    raw = "screenshot: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg== end"
    out = agent_mod._sanitize_tool_content(raw, "browser_screenshot")
    assert "base64," not in out
    assert "[image data omitted]" in out
    # 前后文要保留
    assert out.startswith("screenshot:")
    assert out.endswith("end")


def test_sanitize_strips_multiple_images():
    b64 = "data:image/jpeg;base64," + "A" * 100
    out = agent_mod._sanitize_tool_content(f"{b64} middle {b64}", "x")
    assert out.count("[image data omitted]") == 2


def test_sanitize_leaves_normal_text_alone():
    raw = "CPU 12% / MEM 40%"
    assert agent_mod._sanitize_tool_content(raw, "system_info") == raw


def test_sanitize_truncates_long_output():
    raw = "x" * 12_000
    out = agent_mod._sanitize_tool_content(raw, "browser_extract")
    assert len(out) < 12_000
    assert "truncated from 12000 chars" in out


def test_sanitize_empty_content_is_noop():
    assert agent_mod._sanitize_tool_content("", "x") == ""
    assert agent_mod._sanitize_tool_content(None, "x") is None


# ---------------------------------------------------------------------------
# _extract_tool_calls — 不支持原生 function calling 的模型走这条回退路径
# ---------------------------------------------------------------------------


def test_extract_tool_calls_from_embedded_json():
    text = '好的，我来算一下：{"tool": "calculator", "args": {"expression": "1+1"}}'
    calls = agent_mod._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "calculator"
    assert calls[0]["function"]["arguments"] == '{"expression": "1+1"}'
    assert calls[0]["id"] == "call_0"


def test_extract_tool_calls_handles_whitespace_variants():
    text = '{"tool" : "web_search" , "args" : { "query" : "jarvis" } }'
    calls = agent_mod._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "web_search"


def test_extract_tool_calls_returns_empty_for_plain_text():
    assert agent_mod._extract_tool_calls("你好，有什么可以帮您？") == []


def test_extract_tool_calls_keeps_multiple_calls_in_order():
    text = (
        '{"tool": "a", "args": {"n": 1}}'
        ' then '
        '{"tool": "b", "args": {"n": 2}}'
    )
    calls = agent_mod._extract_tool_calls(text)
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert [c["id"] for c in calls] == ["call_0", "call_1"]


def test_extract_tool_calls_fallback_handles_nested_braces():
    """主正则只支持一层嵌套，深层嵌套靠 rfind 回退分支兜住。"""
    text = '{"tool": "outer", "args": {"payload": {"deep": {"deeper": 1}}}}'
    calls = agent_mod._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "outer"


def test_extract_tool_calls_ignores_malformed_json():
    assert agent_mod._extract_tool_calls('{"tool": "x", "args": {oops}') == []
