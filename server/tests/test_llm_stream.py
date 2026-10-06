"""Phase 17.1 tests — real streaming with tool-call reassembly.

Run with::

    ./.venv/Scripts/python.exe -m pytest tests/test_llm_stream.py -v

Covers:
  * ``ToolCallAccumulator`` — reassembling streamed ``delta.tool_calls``
  * ``call_llm_stream`` — chunk ordering, fallback policy, recovery path
  * ``call_llm(stream=True)`` — backward-compatible text-only shim

The tests are deliberately loop-agnostic (plain ``asyncio.run``) so they do
not depend on the ``anyio`` pytest plugin being registered.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest

# Ensure the project root (server/) and libs/ are on sys.path.
_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)
_libs_dir = os.path.join(_server_root, "libs")
if os.path.isdir(_libs_dir) and _libs_dir not in sys.path:
    sys.path.insert(0, _libs_dir)

from app import llm  # noqa: E402
from app.llm import LLMChunk, LLMError, ToolCallAccumulator  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — SSE event builders
# ---------------------------------------------------------------------------


def _text(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


def _tool_delta(
    index: int | None,
    *,
    name: str | None = None,
    args: Any = None,
    call_id: str | None = None,
) -> dict:
    """One streamed tool_call fragment."""
    call: dict = {}
    if index is not None:
        call["index"] = index
    if call_id is not None:
        call["id"] = call_id
        call["type"] = "function"
    fn: dict = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    call["function"] = fn
    return {"choices": [{"delta": {"tool_calls": [call]}, "finish_reason": None}]}


def _finish(reason: str = "stop") -> dict:
    return {"choices": [{"delta": {}, "finish_reason": reason}]}


# ---------------------------------------------------------------------------
# Helpers — patching
# ---------------------------------------------------------------------------


def _profile(pid: str = "p1", tier: str = "mid", model: str = "test-model") -> dict:
    return {
        "id": pid,
        "tier": tier,
        "model": model,
        "base_url": "http://fake.local/v1",
        "api_key": "sk-test",
    }


@pytest.fixture
def recorded(monkeypatch) -> list[dict]:
    """Capture cost-recording calls instead of writing to the database."""
    calls: list[dict] = []

    async def _fake_record(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(llm, "_record_call", _fake_record)
    return calls


def _install_chain(monkeypatch, chain: list[dict]) -> None:
    async def _resolve(tier: str) -> list[dict]:
        return chain

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)


def _install_sse(monkeypatch, events: list[dict], error: Exception | None = None) -> None:
    """Script the SSE source.

    If ``error`` is given it is raised *after* every scripted event has been
    yielded — this is how a mid-stream provider failure is simulated.
    """

    async def _gen(base_url, headers, payload, timeout):
        for event in events:
            yield event
        if error is not None:
            raise error

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)


def _install_recovery(monkeypatch, calls: list[dict]) -> list[dict]:
    """Patch the non-streaming tool-call recovery; returns the payload log."""
    payloads: list[dict] = []

    async def _recover(base_url, headers, payload) -> list[dict]:
        payloads.append(payload)
        return calls

    monkeypatch.setattr(llm, "_fetch_tool_calls_non_stream", _recover)
    return payloads


def _run(agen) -> list[LLMChunk]:
    """Drain an async generator synchronously."""

    async def _drain():
        return [chunk async for chunk in agen]

    return asyncio.run(_drain())


async def _drain_text(agen) -> list[str]:
    return [part async for part in agen]


def _msgs() -> list[dict]:
    return [{"role": "user", "content": "hello"}]


# ===========================================================================
# ToolCallAccumulator
# ===========================================================================


def test_accumulator_concatenates_argument_fragments():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "id": "call_a", "type": "function",
               "function": {"name": "web_search", "arguments": '{"qu'}}])
    acc.feed([{"index": 0, "function": {"arguments": 'ery": "pyth'}}])
    acc.feed([{"index": 0, "function": {"arguments": 'on"}'}}])

    calls, error = acc.build()

    assert error is None
    assert len(calls) == 1
    assert calls[0]["id"] == "call_a"
    assert calls[0]["function"]["name"] == "web_search"
    # arguments must be a JSON *string*, matching call_llm's contract
    assert isinstance(calls[0]["function"]["arguments"], str)
    assert json.loads(calls[0]["function"]["arguments"]) == {"query": "python"}


def test_accumulator_dedupes_repeated_function_name():
    """Some gateways repeat function.name on every chunk."""
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "web_search", "arguments": "{"}}])
    acc.feed([{"index": 0, "function": {"name": "web_search", "arguments": "}"}}])

    calls, error = acc.build()

    assert error is None
    assert calls[0]["function"]["name"] == "web_search"


def test_accumulator_handles_missing_index():
    acc = ToolCallAccumulator()
    acc.feed([{"function": {"name": "calc", "arguments": '{"a"'}}])
    acc.feed([{"function": {"arguments": ": 1}"}}])

    calls, error = acc.build()

    assert error is None
    assert calls[0]["function"]["name"] == "calc"
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 1}


def test_accumulator_accepts_dict_arguments():
    """A non-conforming gateway may send the finished object at once."""
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "calc", "arguments": {"a": 1}}}])

    calls, error = acc.build()

    assert error is None
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 1}


def test_accumulator_tracks_parallel_calls_by_index():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "id": "c0", "function": {"name": "a", "arguments": '{"x"'}},
              {"index": 1, "id": "c1", "function": {"name": "b", "arguments": '{"y"'}}])
    acc.feed([{"index": 1, "function": {"arguments": ': 2}'}},
              {"index": 0, "function": {"arguments": ": 1}"}}])

    calls, error = acc.build()

    assert error is None
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"x": 1}
    assert json.loads(calls[1]["function"]["arguments"]) == {"y": 2}


def test_accumulator_empty_arguments_become_empty_object():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "list_files"}}])

    calls, error = acc.build()

    assert error is None
    assert calls[0]["function"]["arguments"] == "{}"


def test_accumulator_reports_invalid_json():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "calc", "arguments": '{"a": '}}])

    calls, error = acc.build()

    assert calls == []
    assert error is not None and "invalid arguments JSON" in error


def test_accumulator_reports_missing_name():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "id": "c0", "function": {"arguments": "{}"}}])

    calls, error = acc.build()

    assert calls == []
    assert error is not None and "empty function name" in error


def test_accumulator_rejects_non_object_arguments():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "calc", "arguments": "[1, 2]"}}])

    calls, error = acc.build()

    assert calls == []
    assert error is not None and "not an object" in error


def test_accumulator_generates_id_when_absent():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "function": {"name": "calc", "arguments": "{}"}}])

    calls, _ = acc.build()

    assert calls[0]["id"] == "call_0"


def test_accumulator_ignores_malformed_input():
    acc = ToolCallAccumulator()
    assert acc.empty

    acc.feed("not a list")          # wrong container type
    acc.feed([None, 42, "str"])     # wrong element types
    acc.feed([{"index": 0, "function": "not a dict"}])

    assert acc.empty
    assert acc.build() == ([], None)


# ===========================================================================
# call_llm_stream — happy path
# ===========================================================================


def test_stream_yields_text_tool_calls_and_finish(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [
        _text("我来"),
        _text("查一下"),
        _tool_delta(0, name="web_search", args='{"query"', call_id="c0"),
        _tool_delta(0, args=': "python"}'),
        _finish("tool_calls"),
    ])

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert [c.kind for c in chunks] == ["text", "text", "tool_calls", "finish"]
    assert "".join(c.delta for c in chunks if c.kind == "text") == "我来查一下"
    assert chunks[2].tool_calls[0]["function"]["name"] == "web_search"
    assert chunks[3].finish_reason == "tool_calls"


def test_stream_text_only_response(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [_text("hi"), _finish("stop")])

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert [c.kind for c in chunks] == ["text", "finish"]
    assert chunks[0].delta == "hi"
    assert chunks[-1].finish_reason == "stop"


def test_stream_cost_records_text_and_tool_arguments(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [
        _text("abc"),
        _tool_delta(0, name="calc", args='{"a": 1}', call_id="c0"),
        _finish("tool_calls"),
    ])

    _run(llm.call_llm_stream(_msgs()))

    assert len(recorded) == 1
    # 3 chars of text + len('{"a": 1}') == 8 chars of tool arguments
    assert recorded[0]["output_chars"] == 3 + len('{"a": 1}')
    assert recorded[0]["success"] is True


# ===========================================================================
# call_llm_stream — fallback policy
# ===========================================================================


def test_stream_falls_back_when_first_profile_fails_before_first_chunk(monkeypatch, recorded):
    """A model-unavailable error before any output must try the next profile."""
    attempts: list[str] = []

    async def _resolve(tier: str) -> list[dict]:
        return [_profile("p1", model="m1"), _profile("p2", model="m2")]

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)

    async def _gen(base_url, headers, payload, timeout):
        attempts.append(payload["model"])
        if payload["model"] == "m1":
            raise LLMError("unavailable", detail="connection refused", status_code=503)
        yield _text("from fallback")
        yield _finish("stop")

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert attempts == ["m1", "m2"]
    assert [c.kind for c in chunks] == ["text", "finish"]
    assert chunks[0].delta == "from fallback"
    # Only the successful attempt is recorded — a profile skipped by the
    # fallback policy is not billed as a failed call (matches call_llm).
    assert [r["profile_id"] for r in recorded] == ["p2"]
    assert recorded[0]["success"] is True


def test_stream_raises_when_all_profiles_unavailable(monkeypatch, recorded):
    attempts: list[str] = []

    async def _resolve(tier: str) -> list[dict]:
        return [_profile("p1", model="m1"), _profile("p2", model="m2")]

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)

    async def _gen(base_url, headers, payload, timeout):
        attempts.append(payload["model"])
        raise LLMError("unavailable", detail="connection refused", status_code=503)
        yield  # pragma: no cover — marks this as an async generator

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    with pytest.raises(LLMError):
        _run(llm.call_llm_stream(_msgs()))

    assert attempts == ["m1", "m2"]


def test_stream_does_not_fall_back_after_text_emitted(monkeypatch, recorded):
    """Once text reached the caller, swapping models would corrupt the message."""
    chain_calls: list[str] = []

    async def _resolve(tier: str) -> list[dict]:
        return [_profile("p1"), _profile("p2")]

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)

    attempts: list[str] = []

    async def _gen(base_url, headers, payload, timeout):
        attempts.append(payload["model"])
        yield _text("partial")
        raise LLMError("unavailable", detail="connection reset", status_code=503)

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    with pytest.raises(LLMError):
        _run(llm.call_llm_stream(_msgs()))

    # Only the first profile was ever contacted.
    assert attempts == ["test-model"]
    assert recorded[-1]["success"] is False


def test_stream_silent_empty_response_falls_back(monkeypatch, recorded):
    models: list[str] = []

    async def _resolve(tier: str) -> list[dict]:
        return [_profile("p1", model="m1"), _profile("p2", model="m2")]

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)

    async def _gen(base_url, headers, payload, timeout):
        models.append(payload["model"])
        if payload["model"] == "m1":
            return  # silently produces nothing
        yield _text("recovered")
        yield _finish("stop")

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert models == ["m1", "m2"]
    assert [c.kind for c in chunks] == ["text", "finish"]
    assert chunks[0].delta == "recovered"


def test_stream_silent_empty_response_raises_without_fallback(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])

    async def _gen(base_url, headers, payload, timeout):
        return
        yield  # pragma: no cover — marks this as an async generator

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    with pytest.raises(LLMError, match="produced no content"):
        _run(llm.call_llm_stream(_msgs()))

    assert recorded[-1]["success"] is False
    assert recorded[-1]["error"] == "empty stream"


# ===========================================================================
# call_llm_stream — tool-call recovery
# ===========================================================================


def test_stream_recovers_tool_calls_when_fragments_broken(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [
        _tool_delta(0, name="web_search", args='{"query": ', call_id="c0"),
        _finish("tool_calls"),
    ])
    recovery_calls = _install_recovery(monkeypatch, [
        {"id": "c0", "type": "function",
         "function": {"name": "web_search", "arguments": '{"query": "python"}'}},
    ])

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert len(recovery_calls) == 1
    assert recovery_calls[0]["stream"] is True  # payload is copied, not mutated
    tool_chunks = [c for c in chunks if c.kind == "tool_calls"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].error is None
    assert tool_chunks[0].tool_calls[0]["function"]["name"] == "web_search"


def test_stream_does_not_recover_after_text_emitted(monkeypatch, recorded):
    """Recovery would re-generate the text the user already saw."""
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [
        _text("查一下"),
        _tool_delta(0, name="web_search", args='{"query": ', call_id="c0"),
        _finish("tool_calls"),
    ])
    recovery_calls = _install_recovery(monkeypatch, [])

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert recovery_calls == []
    tool_chunks = [c for c in chunks if c.kind == "tool_calls"]
    # The loss is surfaced instead of silently swallowed.
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_calls == []
    assert tool_chunks[0].error is not None


# ===========================================================================
# _iter_openai_sse — error surface
# ===========================================================================


def test_sse_preserves_error_body_from_streamed_response(monkeypatch):
    """Regression: ``raise_for_status()`` discards the body of a streamed
    response, which used to hide the gateway's actual error message."""
    seen = {}

    class _Resp:
        status_code = 400

        async def aread(self):
            return b'{"error":{"message":"tools not supported"}}'

    class _StreamCtx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, *args, **kwargs):
            return _StreamCtx()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)

    with pytest.raises(LLMError) as excinfo:
        _run(llm._iter_openai_sse("http://fake.local/v1", {}, {}, 12.5))

    assert excinfo.value.status_code == 400
    assert "tools not supported" in excinfo.value.detail
    assert seen["timeout"] == 12.5


def test_sse_normalises_transport_errors(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, *args, **kwargs):
            raise llm.httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)

    with pytest.raises(LLMError) as excinfo:
        _run(llm._iter_openai_sse("http://fake.local/v1", {}, {}, 1.0))

    assert excinfo.value.status_code == 502
    assert "connection refused" in excinfo.value.detail


# ===========================================================================
# Rate limiting counts as "unavailable" so the router tries a sibling profile
# ===========================================================================


def test_rate_limit_is_treated_as_model_unavailable():
    assert llm._is_model_unavailable(LLMError("busy", status_code=429)) is True
    assert llm._is_model_unavailable(LLMError("bad request", status_code=400)) is False


def test_stream_falls_back_on_rate_limit(monkeypatch, recorded):
    attempts: list[str] = []

    async def _resolve(tier: str) -> list[dict]:
        return [_profile("p1", model="m1"), _profile("p2", model="m2")]

    monkeypatch.setattr(llm, "resolve_profile_chain", _resolve)

    async def _gen(base_url, headers, payload, timeout):
        attempts.append(payload["model"])
        if payload["model"] == "m1":
            raise LLMError("busy", detail="", status_code=429)
        yield _text("ok")
        yield _finish("stop")

    monkeypatch.setattr(llm, "_iter_openai_sse", _gen)

    chunks = _run(llm.call_llm_stream(_msgs()))

    assert attempts == ["m1", "m2"]
    assert chunks[0].delta == "ok"


# ===========================================================================
# call_llm — non-streaming path is unchanged, stream=True is a text shim
# ===========================================================================


def test_call_llm_non_stream_delegates_to_non_stream_response(monkeypatch, recorded):
    _install_chain(monkeypatch, [_profile()])
    sent: list[dict] = []

    async def _fake_non_stream(base_url, headers, payload, **kwargs):
        sent.append(payload)
        return {"content": "ok", "tool_calls": None, "role": "assistant",
                "tier": "mid", "model": payload["model"]}

    monkeypatch.setattr(llm, "_non_stream_response", _fake_non_stream)

    result = asyncio.run(llm.call_llm(_msgs(), stream=False, tools=[{"type": "function"}]))

    assert result["content"] == "ok"
    assert sent[0]["stream"] is False
    assert sent[0]["tools"] == [{"type": "function"}]


def test_call_llm_stream_true_shim_yields_text_only(monkeypatch, recorded):
    """The legacy contract returns text only; tool-call chunks are dropped."""
    _install_chain(monkeypatch, [_profile()])
    _install_sse(monkeypatch, [
        _text("a"),
        _tool_delta(0, name="calc", args="{}", call_id="c0"),
        _text("b"),
        _finish("tool_calls"),
    ])

    async def _collect() -> list[str]:
        agen = await llm.call_llm(_msgs(), stream=True)
        return await _drain_text(agen)

    parts = asyncio.run(_collect())

    assert parts == ["a", "b"]
