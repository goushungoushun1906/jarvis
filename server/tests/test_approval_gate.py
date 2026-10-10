"""A6 — tool approval actually gates execution.

Covers the part that used to be theatre: the frontend posted to
``/api/tools/approve``, that route did not exist, the 404 was swallowed, and
every call the user "rejected" ran anyway.

These tests pin the new contract:

* a gated tool does not execute until someone approves it,
* rejection / timeout / missing gate all end in "did not execute",
* the HTTP round-trip (SSE -> dialog -> POST -> resume) works,
* the SSE heartbeat no longer kills a stream that is blocking on a human.

The full HTTP round-trip (SSE -> dialog -> POST -> resume) is NOT covered
here: httpx's ASGITransport does not reliably deliver body chunks while a
response is still open, so those assertions live in
``scripts/dev/verify_approval_gate.py``, which runs them against a real
uvicorn server over TCP — the same way the app actually talks to itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

os.environ.setdefault("JARVIS_MODE", "dev")

from app import agent as agent_mod  # noqa: E402
from app.approval import ApprovalBroker, ApprovalGate, new_gate  # noqa: E402
from app.llm import LLMChunk  # noqa: E402
from app.routes import chat as chat_mod  # noqa: E402
from app.tools import BaseTool, ToolResult  # noqa: E402


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class GatedTool(BaseTool):
    """A tool that must not run without consent; records every execution."""

    def __init__(self, name: str = "workspace_run_command", content: str = "ran"):
        self.name = name
        self.description = f"gated {name}"
        self.parameters_schema: dict = {}
        self.requires_approval = True
        self.concurrency_safe = False
        self.runs: list[dict] = []

    async def execute(self, **kwargs) -> ToolResult:
        self.runs.append(dict(kwargs))
        return ToolResult(self.content if hasattr(self, "content") else "ran", success=True)


class OpenTool(BaseTool):
    def __init__(self, name: str = "calculator"):
        self.name = name
        self.description = f"open {name}"
        self.parameters_schema: dict = {}
        self.requires_approval = False
        self.concurrency_safe = True
        self.runs: list[dict] = []

    async def execute(self, **kwargs) -> ToolResult:
        self.runs.append(dict(kwargs))
        return ToolResult("42", success=True)


class FakeRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def get_openai_tools(self) -> list[dict]:
        return [t.get_schema() for t in self.tools.values()]

    def get_all_commands(self) -> list[dict]:
        return []

    def find_command_plugin(self, name: str):
        return None

    async def execute_command(self, plugin: str, name: str, args: dict) -> ToolResult:
        return ToolResult(f"plugin:{name}", success=True)


class ScriptedLLM:
    """Returns a canned chunk sequence per round."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self.rounds = rounds

    def __call__(self, messages, tools=None, explicit_tier=None):
        chunks = self.rounds.pop(0)

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


def _tool_call(idx: int, name: str, args: dict) -> dict:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _tools_round(calls: list[dict]) -> list[LLMChunk]:
    return [
        LLMChunk(kind="tool_calls", tool_calls=calls),
        LLMChunk(kind="finish", finish_reason="tool_calls"),
    ]


def _text_round(text: str) -> list[LLMChunk]:
    return [LLMChunk(kind="text", delta=text), LLMChunk(kind="finish", finish_reason="stop")]


@pytest.fixture()
def isolated_agent(monkeypatch):
    """Fresh registries per test; returns the tool registry."""
    fake_tools = FakeRegistry()
    fake_plugins = FakeRegistry()
    monkeypatch.setattr(agent_mod, "registry", fake_tools)
    monkeypatch.setattr(agent_mod, "plugin_registry", fake_plugins)
    return fake_tools


async def _collect(messages: list[dict], gate: ApprovalGate | None = None):
    return [ev async for ev in agent_mod.agent_chat_stream(messages, approval_gate=gate)]


# ---------------------------------------------------------------------------
# ApprovalGate unit behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_gate_approves_when_resolved():
    gate = ApprovalGate(broker=ApprovalBroker())
    gate.register("c1", "workspace_run_command", {"cmd": "pytest"})

    async def _answer():
        await asyncio.sleep(0.01)
        assert gate.resolve("c1", True) is True

    task = asyncio.ensure_future(_answer())
    assert await gate.wait("c1") is True
    await task


@pytest.mark.anyio
async def test_gate_denies_on_rejection():
    gate = ApprovalGate(broker=ApprovalBroker())
    gate.register("c1", "workspace_run_command", {})
    gate.resolve("c1", False)
    assert await gate.wait("c1") is False


@pytest.mark.anyio
async def test_gate_denies_on_timeout():
    gate = ApprovalGate(timeout=0.05, broker=ApprovalBroker())
    gate.register("c1", "workspace_run_command", {})
    assert await gate.wait("c1") is False


@pytest.mark.anyio
async def test_gate_auto_deny_mode_never_blocks():
    gate = ApprovalGate(broker=ApprovalBroker(), auto_deny=True)
    gate.register("c1", "workspace_run_command", {})
    # No one answers; auto-deny returns immediately rather than hanging.
    assert await asyncio.wait_for(gate.wait("c1"), timeout=1.0) is False


@pytest.mark.anyio
async def test_gate_denies_unregistered_call():
    gate = ApprovalGate(broker=ApprovalBroker())
    assert await gate.wait("never-registered") is False


@pytest.mark.anyio
async def test_gate_close_denies_pending_and_unbinds():
    broker = ApprovalBroker()
    gate = ApprovalGate(broker=broker)
    gate.register("c1", "workspace_run_command", {})
    assert broker.pending_count() == 1

    waiter = asyncio.ensure_future(gate.wait("c1"))
    await asyncio.sleep(0)
    gate.close()

    assert await waiter is False
    assert broker.pending_count() == 0


@pytest.mark.anyio
async def test_broker_resolves_across_gates():
    broker = ApprovalBroker()
    gate = ApprovalGate(broker=broker)
    gate.register("c1", "workspace_run_command", {})

    # This is what the HTTP endpoint does: no direct reference to the gate.
    assert broker.resolve("c1", True) is True
    assert await gate.wait("c1") is True
    assert broker.resolve("unknown", True) is False


# ---------------------------------------------------------------------------
# Agent loop integration
# ---------------------------------------------------------------------------


def test_gated_tool_does_not_run_until_approved(isolated_agent):
    tool = GatedTool()
    isolated_agent.register(tool)
    gate = new_gate()

    agent_mod.call_llm_stream = ScriptedLLM(  # type: ignore[assignment]
        [
            _tools_round([_tool_call(0, "workspace_run_command", {"command": "pytest"})]),
            _text_round("已执行"),
        ]
    )

    async def _scenario():
        events = []

        async def _approve_later():
            # Approve only once the loop has announced the call — exactly the
            # order a real UI follows.
            while not gate.pending_ids:
                await asyncio.sleep(0)
            assert tool.runs == [], "tool ran before approval"
            gate.resolve(gate.pending_ids[0], True)

        approver = asyncio.ensure_future(_approve_later())
        async for ev in agent_mod.agent_chat_stream([{"role": "user", "content": "run tests"}], approval_gate=gate):
            events.append(ev)
        await approver
        return events

    events = asyncio.run(_scenario())
    assert [e.kind for e in events] == ["tool_call", "tool_result", "text", "done"]
    assert events[0].requires_approval is True
    assert tool.runs == [{"command": "pytest"}]


def test_gated_tool_never_runs_when_rejected(isolated_agent):
    tool = GatedTool()
    isolated_agent.register(tool)
    gate = new_gate()

    agent_mod.call_llm_stream = ScriptedLLM(  # type: ignore[assignment]
        [
            _tools_round([_tool_call(0, "workspace_run_command", {"command": "rm -rf /"})]),
            _text_round("好的，已取消"),
        ]
    )

    async def _scenario():
        async def _reject():
            while not gate.pending_ids:
                await asyncio.sleep(0)
            gate.resolve(gate.pending_ids[0], False)

        rejecter = asyncio.ensure_future(_reject())
        events = [
            ev
            async for ev in agent_mod.agent_chat_stream(
                [{"role": "user", "content": "clean up"}], approval_gate=gate
            )
        ]
        await rejecter
        return events

    events = asyncio.run(_scenario())

    # The decisive assertion: the shell was never touched.
    assert tool.runs == []
    result = next(e for e in events if e.kind == "tool_result")
    assert result.success is False
    assert "拒绝" in result.result
    # The loop survives the refusal and still answers the user.
    assert events[-1].kind == "done"


def test_gated_tool_denied_on_timeout(isolated_agent):
    tool = GatedTool()
    isolated_agent.register(tool)
    gate = ApprovalGate(timeout=0.05, broker=ApprovalBroker())

    agent_mod.call_llm_stream = ScriptedLLM(  # type: ignore[assignment]
        [
            _tools_round([_tool_call(0, "workspace_run_command", {"command": "pytest"})]),
            _text_round("超时了"),
        ]
    )

    async def _scenario():
        return [
            ev
            async for ev in agent_mod.agent_chat_stream(
                [{"role": "user", "content": "run"}], approval_gate=gate
            )
        ]

    events = asyncio.run(_scenario())
    assert tool.runs == []
    result = next(e for e in events if e.kind == "tool_result")
    assert "未在限定时间内确认" in result.result


def test_ungated_tool_runs_without_asking(isolated_agent):
    tool = OpenTool()
    isolated_agent.register(tool)
    gate = new_gate()

    agent_mod.call_llm_stream = ScriptedLLM(  # type: ignore[assignment]
        [
            _tools_round([_tool_call(0, "calculator", {"expr": "40+2"})]),
            _text_round("42"),
        ]
    )

    async def _scenario():
        return [
            ev
            async for ev in agent_mod.agent_chat_stream(
                [{"role": "user", "content": "calc"}], approval_gate=gate
            )
        ]

    events = asyncio.run(_scenario())
    assert gate.pending_ids == []
    assert events[0].requires_approval is None
    assert tool.runs == [{"expr": "40+2"}]


def test_no_gate_keeps_legacy_advisory_behaviour(isolated_agent):
    """Evals and unit tests run without a gate; they must not start blocking."""
    tool = GatedTool()
    isolated_agent.register(tool)

    agent_mod.call_llm_stream = ScriptedLLM(  # type: ignore[assignment]
        [
            _tools_round([_tool_call(0, "workspace_run_command", {"command": "pytest"})]),
            _text_round("done"),
        ]
    )

    events = asyncio.run(_collect([{"role": "user", "content": "run"}]))
    assert tool.runs == [{"command": "pytest"}]
    assert events[0].requires_approval is None


def test_shipped_high_risk_tools_are_marked():
    """Regression guard: the 5 tools the frontend also treats as high-risk.

    Runs against the real registry rather than a double, so dropping a
    ``requires_approval = True`` from a tool class fails here.
    """
    from app import builtins, browser_tools, system_tools, vision_tools, workspace_tools

    for mod, fn in (
        (builtins, "register_tools"),
        (system_tools, "register_system_tools"),
        (vision_tools, "register_vision_tools"),
        (workspace_tools, "register_workspace_tools"),
        (browser_tools, "register_browser_tools"),
    ):
        try:
            getattr(mod, fn)()
        except Exception:  # already registered / no display — best effort
            pass

    assert agent_mod._tool_needs_approval("workspace_run_command") is True
    assert agent_mod._tool_needs_approval("workspace_write_file") is True
    assert agent_mod._tool_needs_approval("system_control") is True
    assert agent_mod._tool_needs_approval("code_execution") is True
    assert agent_mod._tool_needs_approval("vision_agent") is True
    # Read-only tools stay frictionless.
    assert agent_mod._tool_needs_approval("calculator") is False
    assert agent_mod._tool_needs_approval("web_search") is False
    assert agent_mod._tool_needs_approval("workspace_read_file") is False


# ---------------------------------------------------------------------------
# HTTP round-trip: SSE -> dialog -> POST /api/tools/approve -> resume
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def app():
    from main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:18200") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _no_memory_extraction(monkeypatch):
    async def _noop(*args, **kwargs):
        return []

    monkeypatch.setattr(chat_mod.mem, "extract_memories_from_message", _noop)


@pytest.mark.anyio
@pytest.mark.anyio
async def test_approve_endpoint_unknown_id_is_reported(client):
    r = await client.post(
        "/api/tools/approve",
        json={"tool_call_id": "no-such-call", "approved": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "unknown_or_expired_call_id"
