"""Phase 17.1 端到端验证：真实网关上跑一次流式 + 工具调用。

用法：
    cd server && ./.venv/Scripts/python.exe scripts/dev/verify_llm_stream.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SERVER))

import app.llm as llm  # noqa: E402
from app.database import init_db  # noqa: E402


async def _noop_record(**kwargs) -> None:
    """避免验证调用污染成本看板。"""
    print(
        f"  [cost] model={kwargs.get('model')} tier={kwargs.get('tier')} "
        f"in={kwargs.get('input_chars')} out={kwargs.get('output_chars')} "
        f"{kwargs.get('duration_ms')}ms success={kwargs.get('success')}"
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a math expression.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
]


async def run(label: str, messages: list[dict], tools, tier: str | None = None) -> bool:
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")
    start = time.perf_counter()
    first_text_at: float | None = None
    text_parts: list[str] = []

    try:
        async for chunk in llm.call_llm_stream(messages, tools=tools, explicit_tier=tier):
            elapsed = (time.perf_counter() - start) * 1000

            if chunk.kind == "text":
                if first_text_at is None:
                    first_text_at = elapsed
                    print(f"  [text  ] FIRST at {elapsed:7.0f} ms  <- 首 token 延迟")
                text_parts.append(chunk.delta)
                print(f"  [text  ] +{elapsed:7.0f} ms  {chunk.delta!r}")
            elif chunk.kind == "tool_calls":
                print(f"  [tools ] +{elapsed:7.0f} ms  error={chunk.error!r}")
                for call in chunk.tool_calls or []:
                    print(
                        f"             -> id={call['id']} name={call['function']['name']} "
                        f"args={call['function']['arguments']}"
                    )
            elif chunk.kind == "finish":
                print(f"  [finish] +{elapsed:7.0f} ms  reason={chunk.finish_reason!r}")
    except llm.LLMError as exc:
        print(f"  !! LLMError: {exc.message} (status={exc.status_code})")
        return False

    total = (time.perf_counter() - start) * 1000
    print(f"  --- 总计 {total:.0f} ms，文本 {len(''.join(text_parts))} 字")
    if first_text_at:
        print(f"  --- 首 token {first_text_at:.0f} ms")
    return True


SCENARIOS = {
    "1": (
        "场景 1：纯文本（验证真流式逐字到达）",
        [{"role": "user", "content": "用三句话介绍一下量子计算。"}],
        None,
    ),
    "2": (
        "场景 2：应触发工具调用（验证 tool_calls 分片累积）",
        [{"role": "user", "content": "帮我搜索一下 2026 年诺贝尔物理学奖得主是谁。"}],
        TOOLS,
    ),
    "3": (
        "场景 3：文本 + 工具混合（验证交错顺序）",
        [{"role": "user", "content": "先说一下你打算怎么做，然后计算 (1234 * 5678) + 90。"}],
        TOOLS,
    ),
}


async def main() -> None:
    wanted = sys.argv[1:] or ["1", "2", "3"]

    llm._record_call = _noop_record  # type: ignore[assignment]
    await init_db()

    sys_prompt = {"role": "system", "content": "You are a helpful assistant with tools."}

    for key in wanted:
        label, msgs, tools = SCENARIOS[key]
        ok = await run(label, [sys_prompt, *msgs], tools)
        if not ok and key != wanted[-1]:
            print("  -> 该场景失败（可能触发限流），等待 20s 后继续")
            await asyncio.sleep(20)
        elif key != wanted[-1]:
            await asyncio.sleep(8)  # 避免触发网关限流


if __name__ == "__main__":
    asyncio.run(main())
