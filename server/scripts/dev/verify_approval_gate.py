"""A6 approval gate — end-to-end check against a live uvicorn server.

Why a script and not a pytest case: the assertions below need the SSE
response to stay open while a second, independent HTTP request lands. httpx's
ASGITransport does not deliver body chunks reliably in that situation, so this
boots the real app over TCP and drives it exactly the way the Electron client
does: read ``tool_call`` -> show the dialog -> ``POST /api/tools/approve`` ->
watch the stream resume.

The LLM layer is stubbed, so no tokens are spent; the tool is a no-op double
registered as ``requires_approval``, so nothing risky can run either way.

Usage:
    python scripts/dev/verify_approval_gate.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

# Must precede the app import: DEFAULT_TIMEOUT_SECONDS is read at import time.
os.environ.setdefault("JARVIS_APPROVAL_TIMEOUT", "6")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

os.environ.setdefault("JARVIS_MODE", "dev")

from app import agent as agent_mod  # noqa: E402
from app.llm import LLMChunk  # noqa: E402
from app.routes import chat as chat_mod  # noqa: E402
from app.tools import BaseTool, ToolResult, registry  # noqa: E402

PORT = 18211
BASE = f"http://127.0.0.1:{PORT}"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class NoopGatedTool(BaseTool):
    """Stands in for workspace_run_command — gated, but executes nothing."""

    name = "verify_gated_tool"
    description = "verification double: gated, does nothing"
    parameters_schema: dict = {}
    requires_approval = True
    concurrency_safe = False
    runs: list[dict] = []

    async def execute(self, **kwargs) -> ToolResult:
        NoopGatedTool.runs.append(dict(kwargs))
        return ToolResult("executed", success=True)


def scripted_llm(rounds: list[list[LLMChunk]]):
    """Return a call_llm_stream stand-in replaying `rounds`."""
    remaining = list(rounds)

    def _factory(messages, tools=None, explicit_tier=None):
        chunks = remaining.pop(0)

        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    return _factory


def gated_round(name: str, args: dict) -> list[LLMChunk]:
    tc = {
        "id": "call_verify_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }
    return [
        LLMChunk(kind="tool_calls", tool_calls=[tc]),
        LLMChunk(kind="finish", finish_reason="tool_calls"),
    ]


def text_round(text: str) -> list[LLMChunk]:
    return [LLMChunk(kind="text", delta=text), LLMChunk(kind="finish", finish_reason="stop")]


# ---------------------------------------------------------------------------
# SSE driving
# ---------------------------------------------------------------------------


async def run_stream(on_tool_call=None, approve_after: float | None = None,
                    approve: bool = True, heartbeat: float = 1.0):
    """Drive one /api/chat/stream request; return (events, pings, payloads)."""
    agent_mod.call_llm_stream = scripted_llm(  # type: ignore[assignment]
        [gated_round(NoopGatedTool.name, {"x": 1}), text_round("已处理")]
    )
    chat_mod.HEARTBEAT_INTERVAL_SECONDS = heartbeat

    events: list[str] = []
    pings = 0
    tool_call_payload: dict | None = None
    tool_result_payload: dict | None = None
    approved_ok: list[bool] = []
    started = time.perf_counter()
    first_byte_at: float | None = None

    async with httpx.AsyncClient(timeout=90, trust_env=False, base_url=BASE) as http:
        async with http.stream(
            "POST",
            f"{BASE}/api/chat/stream",
            json={"messages": [{"role": "user", "content": "run"}], "stream": True},
            headers={"Origin": "null"},
        ) as resp:
            assert resp.status_code == 200, resp.status_code
            buf = ""
            async for chunk in resp.aiter_text():
                if first_byte_at is None and chunk:
                    first_byte_at = (time.perf_counter() - started) * 1000
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    if frame.startswith(":"):
                        pings += 1
                        continue
                    name = None
                    data = None
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            name = line[7:]
                        elif line.startswith("data: "):
                            data = line[6:]
                    if not name:
                        continue
                    if name == "tool_call":
                        tool_call_payload = json.loads(data or "{}")["tool_call"]
                    elif name == "tool_result":
                        tool_result_payload = json.loads(data or "{}")["tool_result"]
                    events.append(name)

                    if name == "tool_call" and approve_after is not None:
                        async def _later():
                            await asyncio.sleep(approve_after)
                            r = await http.post(
                                "/api/tools/approve",
                                json={"tool_call_id": tool_call_payload["id"],
                                      "approved": approve},
                            )
                            approved_ok.append(r.json().get("ok"))

                        asyncio.ensure_future(_later())
                    elif name == "tool_call" and on_tool_call is not None:
                        await on_tool_call(http, tool_call_payload, approved_ok)
                if "done" in events:
                    break

    return {
        "events": events,
        "pings": pings,
        "tool_call": tool_call_payload,
        "tool_result": tool_result_payload,
        "approved_ok": approved_ok,
        "first_byte_ms": first_byte_at or 0.0,
        "total_ms": (time.perf_counter() - started) * 1000,
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


async def check_approve_path() -> list[tuple[str, bool, str]]:
    before = len(NoopGatedTool.runs)
    res = await run_stream(approve_after=0.0, approve=True, heartbeat=1.0)
    rows = [
        ("审批通过后工具才执行",
         len(NoopGatedTool.runs) == before + 1,
         f"执行次数 {len(NoopGatedTool.runs) - before}"),
        ("tool_call 标记 requires_approval / awaiting_approval",
         bool(res["tool_call"] and res["tool_call"]["requires_approval"] is True
              and res["tool_call"]["status"] == "awaiting_approval"),
         json.dumps(res["tool_call"], ensure_ascii=False)[:120] if res["tool_call"] else "(无)"),
        ("审批端点接受决定", res["approved_ok"] == [True], f"{res['approved_ok']}"),
        ("事件序列完整", res["events"] == ["tool_call", "tool_result", "text", "done"],
         " -> ".join(res["events"])),
        ("tool_result 成功",
         bool(res["tool_result"] and res["tool_result"]["success"] is True),
         (res["tool_result"] or {}).get("result", "")[:60]),
    ]
    return rows


async def check_reject_path() -> list[tuple[str, bool, str]]:
    before = len(NoopGatedTool.runs)

    async def _reject(http, tool_call, sink):
        r = await http.post("/api/tools/approve",
                            json={"tool_call_id": tool_call["id"], "approved": False})
        sink.append(r.json().get("ok"))

    res = await run_stream(on_tool_call=_reject, heartbeat=1.0)
    result = (res["tool_result"] or {}).get("result", "")
    rows = [
        ("拒绝后工具一次都没执行",
         len(NoopGatedTool.runs) == before,
         f"执行次数 {len(NoopGatedTool.runs) - before}"),
        ("拒绝被后端接受", res["approved_ok"] == [True], f"{res['approved_ok']}"),
        ("tool_result 说明是用户拒绝",
         "拒绝" in result and res["tool_result"]["success"] is False, result[:70]),
        ("拒绝后循环仍正常收尾", res["events"][-1] == "done", " -> ".join(res["events"])),
    ]
    return rows


async def check_heartbeat_survives_wait() -> list[tuple[str, bool, str]]:
    """The regression that motivated the asyncio.wait rewrite.

    Under the old ``wait_for(agen.__anext__(), 15)`` the first heartbeat would
    have cancelled the generator and killed the stream mid-approval.
    """
    before = len(NoopGatedTool.runs)
    res = await run_stream(approve_after=2.5, approve=True, heartbeat=0.5)
    rows = [
        ("等待审批期间心跳照常下发", res["pings"] >= 2, f"pings={res['pings']}"),
        ("心跳未打断被阻塞的流",
         res["events"] == ["tool_call", "tool_result", "text", "done"],
         " -> ".join(res["events"])),
        ("延迟审批后仍执行成功",
         len(NoopGatedTool.runs) == before + 1 and res["approved_ok"] == [True],
         f"执行 {len(NoopGatedTool.runs) - before} 次, approved_ok={res['approved_ok']}"),
        ("首字节远早于结束（未被缓冲）",
         0 < res["first_byte_ms"] < res["total_ms"] * 0.9,
         f"首字节 +{res['first_byte_ms']:.0f}ms / 总 {res['total_ms']:.0f}ms"),
    ]
    return rows


async def check_timeout_auto_deny() -> list[tuple[str, bool, str]]:
    """Nobody answers: the call must be denied, not executed, not hang forever."""
    before = len(NoopGatedTool.runs)
    # No approval is ever sent; JARVIS_APPROVAL_TIMEOUT is 6s for this run.
    res = await run_stream(approve_after=None, heartbeat=1.0)
    result = (res["tool_result"] or {}).get("result", "")
    rows = [
        ("无人应答时工具不执行",
         len(NoopGatedTool.runs) == before,
         f"执行次数 {len(NoopGatedTool.runs) - before}"),
        ("超时后自动拒绝并说明原因",
         "未在限定时间内确认" in result, result[:70]),
        ("超时后循环仍收尾", res["events"][-1] == "done", " -> ".join(res["events"])),
    ]
    return rows


async def check_unknown_call_id() -> list[tuple[str, bool, str]]:
    async with httpx.AsyncClient(timeout=15, trust_env=False, base_url=BASE) as http:
        r = await http.post("/api/tools/approve",
                            json={"tool_call_id": "no-such-call", "approved": True})
        body = r.json()
        return [("未知 call_id 被明确报告",
                 r.status_code == 200 and body.get("ok") is False
                 and body.get("reason") == "unknown_or_expired_call_id",
                 json.dumps(body, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def main_async() -> int:
    registry.register(NoopGatedTool())
    # Keep the run offline.
    async def _noop(*a, **k):
        return []

    chat_mod.mem.extract_memories_from_message = _noop  # type: ignore[assignment]

    from main import app as fastapi_app

    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=PORT,
                            log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())

    deadline = time.time() + 60
    while not server.started and time.time() < deadline:
        await asyncio.sleep(0.2)
    if not server.started:
        print("服务未启动，中止", flush=True)
        return 1

    try:
        rows: list[tuple[str, bool, str]] = []
        for name, fn in (
            ("审批通过路径", check_approve_path),
            ("拒绝路径", check_reject_path),
            ("心跳不打断阻塞", check_heartbeat_survives_wait),
            ("超时自动拒绝", check_timeout_auto_deny),
            ("未知 call_id", check_unknown_call_id),
        ):
            print(f"\n--- {name} ---", flush=True)
            for label, ok, detail in await fn():
                print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
                      + (f"  ({detail})" if detail else ""), flush=True)
                rows.append((label, ok, detail))

        failed = sum(1 for _, ok, _ in rows if not ok)
        print(f"\n共 {len(rows)} 项，{'全部通过' if failed == 0 else f'{failed} 项未通过'}",
              flush=True)
        return 0 if failed == 0 else 1
    finally:
        # The lifespan starts an MCP server as a *child process* (stdio
        # transport). Leave it running and it keeps our stdout handle open, so
        # the shell waits on a pipe nobody will ever close.
        try:
            from app.mcp_client import mcp_client

            await mcp_client.shutdown()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        # uvicorn's graceful shutdown waits on the (already closed) SSE
        # connections and then trips over its own cancel scopes; the checks
        # are done by now, so just take the server down.
        server.should_exit = True
        task.cancel()


if __name__ == "__main__":
    rc = asyncio.run(main_async())
    # uvicorn leaves non-daemon threads behind after a cancelled serve(), and
    # the interpreter waits on them forever. The verdict is already printed,
    # so exit hard rather than hang a CI job.
    sys.stdout.flush()
    os._exit(rc)
