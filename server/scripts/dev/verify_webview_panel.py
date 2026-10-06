"""Phase 17.8 dev check — browser tool -> right-hand display panel contract.

Two things must hold for the panel to mirror a browsed page:

1. ``browser_open`` actually works in the *project venv* (it silently did not:
   requirements.txt pinned a playwright version that does not exist on PyPI),
   and reports the resolved URL plus whether the page may be framed.
2. That metadata survives the agent event hop, which is what the renderer reads.

Run:  ./.venv/Scripts/python.exe -u scripts/dev/verify_webview_panel.py [url]
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://app.pavo-ai.work/"


async def check_registry_path() -> bool:
    """Exercise the real dispatch path the agent uses."""
    from app.agent import _execute_tool_call
    from app.builtins import register_tools
    from app.browser_tools import register_browser_tools
    from app.tools import registry

    register_tools()
    register_browser_tools()

    tool = registry.get("browser_open")
    print(f"  registry.get('browser_open') -> {type(tool).__name__ if tool else None}")
    if tool is None:
        return False

    start = time.perf_counter()
    result = await _execute_tool_call("browser_open", {"url": TARGET, "max_length": 300})
    elapsed = (time.perf_counter() - start) * 1000

    print(f"  success    : {result.success}  ({elapsed:.0f} ms)")
    print(f"  content    : {result.content[:110]!r}")
    meta = result.metadata or {}
    print(f"  metadata.url          = {meta.get('url')!r}")
    print(f"  metadata.title        = {meta.get('title')!r}")
    print(f"  metadata.embeddable   = {meta.get('embeddable')!r}")
    print(f"  metadata.block_reason = {meta.get('embed_block_reason')!r}")

    ok = bool(result.success and meta.get("url"))
    print(f"  -> display panel can mirror this page: {ok}")
    return ok


async def check_event_hop() -> bool:
    """ToolResult.metadata must reach the tool_result AgentEvent verbatim."""
    from app.agent import _tool_result_event
    from app.tools import ToolResult

    meta = {"url": "https://x.example/", "embeddable": False, "embed_block_reason": "test"}
    tc = {"id": "call_1", "function": {"name": "browser_open", "arguments": "{}"}}
    event = _tool_result_event(tc, ToolResult("body", metadata=meta), 12)

    print(f"  event.kind     = {event.kind!r}")
    print(f"  event.metadata = {event.metadata!r}")
    ok = event.metadata == meta
    print(f"  -> metadata reaches the SSE payload: {ok}")
    return ok


async def main() -> int:
    print(f"\n{'=' * 70}\n1) browser_open through the real registry (TARGET={TARGET})\n{'=' * 70}")
    ok1 = await check_registry_path()

    print(f"\n{'=' * 70}\n2) metadata hop to the tool_result event\n{'=' * 70}")
    ok2 = await check_event_hop()

    print(f"\n{'=' * 70}\n结果: {'全部通过' if ok1 and ok2 else '存在失败项'}\n{'=' * 70}")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
