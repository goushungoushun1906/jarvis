"""Phase 17.3 — SSE protocol tests for ``/api/chat/stream``.

The agent layer is fully mocked; these tests verify the HTTP contract:

* named ``event:`` lines paired with legacy ``data:`` JSON lines,
* ``requires_approval`` supplied by the backend tool registry,
* ``data: [DONE]`` terminator on both success and error paths,
* 15s heartbeat comments on idle streams,
* assistant message persisted only on the ``done`` path.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, AsyncGenerator, Generator

import pytest
from httpx import ASGITransport, AsyncClient

_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

os.environ.setdefault("JARVIS_MODE", "dev")

from app.agent import AgentEvent  # noqa: E402
from app.routes import chat as chat_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def app():
    from main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _no_memory_extraction(monkeypatch):
    """Keep tests offline: memory extraction may call the LLM."""

    async def _noop(*args, **kwargs):
        return []

    monkeypatch.setattr(chat_mod.mem, "extract_memories_from_message", _noop)


def _scripted_stream(events: list[AgentEvent], delay: float = 0.0):
    """Build a fake agent_chat_stream returning the given events."""

    def _factory(messages):
        async def _gen():
            if delay:
                await asyncio.sleep(delay)
            for ev in events:
                if delay:
                    await asyncio.sleep(delay)
                yield ev

        return _gen()

    return _factory


def parse_sse(text: str) -> list[dict]:
    """Parse raw SSE text into ``{event, data}`` dicts in order."""
    frames: list[dict] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event_name: str | None = None
        data: str | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:]
        frames.append({"event": event_name, "data": data})
    return frames


async def stream_chat(client: AsyncClient, payload: dict) -> tuple[int, str, dict]:
    """POST /api/chat/stream and collect the full SSE text plus headers."""
    async with client.stream("POST", "/api/chat/stream", json=payload) as resp:
        assert resp.status_code == 200
        text = ""
        async for chunk in resp.aiter_text():
            text += chunk
        return resp.status_code, text, dict(resp.headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_text_events_use_named_sse_with_legacy_data(client, monkeypatch):
    events = [
        AgentEvent(kind="text", delta="你好"),
        AgentEvent(kind="text", delta="，世界"),
        AgentEvent(kind="done", content="你好，世界", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    frames = parse_sse(text)
    assert [f["event"] for f in frames] == ["text", "text", "done", None]
    # Legacy contract: data lines still carry {"content": ...}
    assert json.loads(frames[0]["data"]) == {"content": "你好"}
    assert json.loads(frames[1]["data"]) == {"content": "，世界"}
    # Terminator
    assert frames[-1]["data"] == "[DONE]"


@pytest.mark.anyio
async def test_tool_events_carry_backend_approval_flag(client, monkeypatch):
    tool = chat_mod.tool_registry.get("web_search")
    assert tool is not None, "web_search must be registered by app startup"
    monkeypatch.setattr(tool, "requires_approval", False)

    events = [
        AgentEvent(kind="tool_call", call_id="c1", name="web_search", args={"query": "x"}),
        AgentEvent(kind="tool_result", call_id="c1", name="web_search",
                   result="found", success=True, duration_ms=42),
        AgentEvent(kind="done", content="ok", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    frames = parse_sse(text)
    call_payload = json.loads(frames[0]["data"])["tool_call"]
    assert call_payload == {
        "id": "c1",
        "tool": "web_search",
        "args": {"query": "x"},
        "status": "running",
        "requires_approval": False,
    }
    result_payload = json.loads(frames[1]["data"])["tool_result"]
    assert result_payload["id"] == "c1"
    assert result_payload["success"] is True
    assert result_payload["status"] == "success"
    assert result_payload["duration_ms"] == 42


@pytest.mark.anyio
async def test_requires_approval_true_comes_from_registry(client, monkeypatch):
    tool = chat_mod.tool_registry.get("web_search")
    monkeypatch.setattr(tool, "requires_approval", True)

    events = [
        AgentEvent(kind="tool_call", call_id="c1", name="web_search", args={}),
        AgentEvent(kind="done", content="ok", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    call_payload = json.loads(parse_sse(text)[0]["data"])["tool_call"]
    assert call_payload["requires_approval"] is True


@pytest.mark.anyio
async def test_done_event_aggregates_tool_log(client, monkeypatch):
    events = [
        AgentEvent(kind="tool_call", call_id="c1", name="calc", args={"expression": "1+1"}),
        AgentEvent(kind="tool_result", call_id="c1", name="calc",
                   result="2", success=True, duration_ms=5),
        AgentEvent(kind="done", content="答案是 2", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    done_frame = next(f for f in parse_sse(text) if f["event"] == "done")
    payload = json.loads(done_frame["data"])
    assert payload["conversation_id"] is None
    assert payload["tool_calls"] == [
        {"call_id": "c1", "tool": "calc", "args": {"expression": "1+1"},
         "result": "2", "success": True}
    ]


@pytest.mark.anyio
async def test_error_event_terminates_stream(client, monkeypatch):
    events = [
        AgentEvent(kind="text", delta="部分文本"),
        AgentEvent(kind="error", message="LLM Error: rate limited"),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    frames = parse_sse(text)
    assert frames[-2]["event"] == "error"
    assert json.loads(frames[-2]["data"]) == {"error": "LLM Error: rate limited"}
    assert frames[-1]["data"] == "[DONE]"
    # No done event after an error
    assert all(f["event"] != "done" for f in frames)


@pytest.mark.anyio
async def test_heartbeat_on_idle_stream(client, monkeypatch):
    monkeypatch.setattr(chat_mod, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    events = [AgentEvent(kind="done", content="ok", tool_log=[])]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events, delay=0.3))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    assert ": ping" in text


@pytest.mark.anyio
async def test_heartbeat_absent_on_fast_stream(client, monkeypatch):
    monkeypatch.setattr(chat_mod, "HEARTBEAT_INTERVAL_SECONDS", 15.0)
    events = [AgentEvent(kind="done", content="ok", tool_log=[])]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    assert ": ping" not in text


@pytest.mark.anyio
async def test_assistant_message_persisted_on_done(client, monkeypatch):
    conv = (await client.post("/api/conversations", json={"title": "sse-test"})).json()

    events = [
        AgentEvent(kind="text", delta="最终"),
        AgentEvent(kind="text", delta="回答"),
        AgentEvent(kind="done", content="最终回答", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(
        client,
        {"messages": [{"role": "user", "content": "hi"}], "conversation_id": conv["id"]},
    )
    assert "[DONE]" in text

    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert assistant_msgs and assistant_msgs[-1]["content"] == "最终回答"


@pytest.mark.anyio
async def test_assistant_message_not_persisted_on_error(client, monkeypatch):
    conv = (await client.post("/api/conversations", json={"title": "sse-err"})).json()

    events = [AgentEvent(kind="error", message="boom")]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(
        client,
        {"messages": [{"role": "user", "content": "hi"}], "conversation_id": conv["id"]},
    )
    assert "[DONE]" in text

    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert assistant_msgs == []


@pytest.mark.anyio
async def test_every_data_line_is_legacy_parseable(client, monkeypatch):
    """The old frontend parser only reads ``data:`` lines — all must be valid."""
    events = [
        AgentEvent(kind="text", delta="a"),
        AgentEvent(kind="tool_call", call_id="c1", name="t", args={}),
        AgentEvent(kind="tool_result", call_id="c1", name="t", result="r",
                   success=True, duration_ms=1),
        AgentEvent(kind="done", content="a", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    for frame in parse_sse(text):
        assert frame["data"] is not None
        if frame["data"] == "[DONE]":
            continue
        parsed = json.loads(frame["data"])
        assert isinstance(parsed, dict)
        # Old parser branches must find a known key
        assert any(k in parsed for k in ("content", "tool_call", "tool_result", "error", "conversation_id"))


@pytest.mark.anyio
async def test_response_headers_disable_buffering(client, monkeypatch):
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(
        [AgentEvent(kind="done", content="ok", tool_log=[])]
    ))

    _, _, headers = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    assert headers.get("x-accel-buffering") == "no"
    assert "text/event-stream" in headers.get("content-type", "")


@pytest.mark.anyio
async def test_tool_result_event_forwards_tool_metadata(client, monkeypatch):
    """Phase 17.8 — the display panel learns the browsed URL from this frame."""
    events = [
        AgentEvent(kind="tool_call", call_id="c9", name="browser_open",
                   args={"url": "https://app.pavo-ai.work/"}),
        AgentEvent(kind="tool_result", call_id="c9", name="browser_open",
                   result="Title: Pavo", success=True, duration_ms=1200,
                   metadata={"url": "https://app.pavo-ai.work/", "title": "Pavo",
                             "embeddable": False,
                             "embed_block_reason": "该站点设置了 X-Frame-Options: DENY"}),
        AgentEvent(kind="done", content="ok", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "打开"}]})

    result_payload = json.loads(parse_sse(text)[1]["data"])["tool_result"]
    assert result_payload["metadata"]["url"] == "https://app.pavo-ai.work/"
    assert result_payload["metadata"]["title"] == "Pavo"
    assert result_payload["metadata"]["embeddable"] is False
    assert "X-Frame-Options" in result_payload["metadata"]["embed_block_reason"]


@pytest.mark.anyio
async def test_tool_result_metadata_never_breaks_serialisation(client, monkeypatch):
    """A tool must not be able to wedge the stream by reporting an exotic value."""
    from datetime import datetime, timezone

    events = [
        AgentEvent(kind="tool_result", call_id="c1", name="system_info",
                   result="ok", success=True,
                   metadata={"captured_at": datetime.now(timezone.utc), "nested": {"a": 1}}),
        AgentEvent(kind="done", content="ok", tool_log=[]),
    ]
    monkeypatch.setattr(chat_mod, "agent_chat_stream", _scripted_stream(events))

    _, text, _ = await stream_chat(client, {"messages": [{"role": "user", "content": "hi"}]})

    # `default=str` in _sse keeps non-JSON-native values from raising.
    payload = json.loads(parse_sse(text)[0]["data"])["tool_result"]
    assert isinstance(payload["metadata"]["captured_at"], str)
    assert payload["metadata"]["nested"] == {"a": 1}
