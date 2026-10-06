"""Phase 17.2 端到端验证：真实网关上跑 agent_chat_stream 全事件链。

用法：
    cd server && ./.venv/Scripts/python.exe scripts/dev/verify_agent_stream.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SERVER))

import app.agent as agent_mod  # noqa: E402
import app.llm as llm  # noqa: E402
from app.builtins import register_tools  # noqa: E402
from app.database import init_db  # noqa: E402


async def _noop_record(**kwargs) -> None:
    """避免验证调用污染成本看板。"""


async def run(label: str, messages: list[dict]) -> None:
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")
    start = time.perf_counter()
    first_event_at: float | None = None
    text_len = 0
    tool_results = 0

    async for ev in agent_mod.agent_chat_stream(messages):
        elapsed = (time.perf_counter() - start) * 1000
        if first_event_at is None and ev.kind != "error":
            first_event_at = elapsed
            print(f"  [first ] +{elapsed:7.0f} ms  kind={ev.kind}")

        if ev.kind == "text":
            text_len += len(ev.delta)
            print(f"  [text  ] +{elapsed:7.0f} ms  {ev.delta!r}")
        elif ev.kind == "tool_call":
            print(f"  [call  ] +{elapsed:7.0f} ms  {ev.name} args={ev.args}")
        elif ev.kind == "tool_result":
            tool_results += 1
            preview = ev.result[:100].replace("\n", " ")
            print(
                f"  [result] +{elapsed:7.0f} ms  {ev.name} ok={ev.success} "
                f"{ev.duration_ms}ms  {preview!r}"
            )
        elif ev.kind == "done":
            print(
                f"  [done  ] +{elapsed:7.0f} ms  text={len(ev.content)} chars, "
                f"tool_log={len(ev.tool_log)} entries"
            )
        elif ev.kind == "error":
            print(f"  [ERROR ] +{elapsed:7.0f} ms  {ev.message}")

    total = (time.perf_counter() - start) * 1000
    print(f"  --- 总计 {total:.0f} ms，流内文本 {text_len} 字，工具结果 {tool_results} 个")


async def main() -> None:
    llm._record_call = _noop_record  # type: ignore[assignment]
    await init_db()
    await asyncio.to_thread(register_tools)

    names = [t.name for t in agent_mod.registry.list_tools()]
    safe = [t.name for t in agent_mod.registry.list_tools() if t.concurrency_safe]
    print(f"注册工具 {len(names)} 个；并发安全 {len(safe)} 个：{safe}")

    sys_prompt = {
        "role": "system",
        "content": (
            "You are JARVIS, a helpful assistant with tools. "
            "You may narrate briefly before calling a tool."
        ),
    }

    await run(
        "场景 1：文本 + 工具 + 继续文本（完整事件链）",
        [
            sys_prompt,
            {"role": "user", "content": "先说一句你打算怎么做，然后用计算器算 (1234 * 5678) + 90，并告诉我结果。"},
        ],
    )

    await run(
        "场景 2：纯文本（验证首 token 不再被工具管线阻塞）",
        [
            sys_prompt,
            {"role": "user", "content": "用两句话介绍你自己。"},
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
