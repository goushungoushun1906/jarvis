"""Agent loop: tool-aware chat with function calling.

Phase 17.2 — two entry points:

* :func:`agent_chat_stream` — async generator yielding :class:`AgentEvent`
  objects in real time (text deltas, tool calls, tool results, done/error).
  Text comes from :func:`app.llm.call_llm_stream`, so the first token reaches
  the caller at the model's own time-to-first-token. Read-only tools flagged
  ``concurrency_safe`` run in parallel within a round.
* :func:`agent_chat` — legacy blocking wrapper that drains the stream and
  returns ``(final_text, tool_log)``. Kept for existing callers (routes,
  evals); new code should consume the stream directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Literal

from .llm import call_llm_stream, LLMError
from .tools import BaseTool, registry, ToolResult
from .plugins.registry import registry as plugin_registry

logger = logging.getLogger("jarvis")

MAX_TOOL_ROUNDS = 5  # Prevent infinite tool loops


# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------


@dataclass
class AgentEvent:
    """One real-time event produced by :func:`agent_chat_stream`.

    ``kind`` is one of:

    * ``"text"``       — ``delta`` carries an incremental text fragment.
    * ``"tool_call"``  — the model requested a tool: ``call_id`` / ``name`` /
      ``args`` (parsed dict). Emitted *before* execution starts.
    * ``"tool_result"`` — execution finished: ``call_id`` / ``name`` /
      ``result`` / ``success`` / ``duration_ms``.
    * ``"done"``       — loop finished normally: ``content`` is the final
      assistant text, ``tool_log`` the complete tool log.
    * ``"error"``      — fatal error: ``message``. No ``done`` follows.
    """

    kind: Literal["text", "tool_call", "tool_result", "done", "error"]
    delta: str = ""
    call_id: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    result: str = ""
    success: bool = True
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)
    content: str = ""
    message: str = ""
    tool_log: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------
# Streaming agent loop
# ------------------------------------------------------------------


async def agent_chat_stream(messages: list[dict]) -> AsyncGenerator[AgentEvent, None]:
    """Run the agent loop, yielding :class:`AgentEvent` in real time."""
    # Build combined tool list: built-in registry + enabled plugins
    all_tools = registry.get_openai_tools() + plugin_registry.get_all_commands()

    msgs = messages.copy()
    tool_log: list[dict] = []

    for _round in range(MAX_TOOL_ROUNDS):
        # ---- 1. Stream one LLM completion -----------------------------
        text_parts: list[str] = []
        native_tool_calls: list[dict] = []
        stream_error: str | None = None

        try:
            async for chunk in call_llm_stream(msgs, tools=all_tools or None):
                if chunk.kind == "text":
                    text_parts.append(chunk.delta)
                    yield AgentEvent(kind="text", delta=chunk.delta)
                elif chunk.kind == "tool_calls":
                    if chunk.tool_calls:
                        native_tool_calls.extend(chunk.tool_calls)
                    elif chunk.error:
                        stream_error = chunk.error
                elif chunk.kind == "finish" and chunk.finish_reason:
                    logger.debug("LLM stream finish_reason=%s", chunk.finish_reason)
        except LLMError as e:
            yield AgentEvent(kind="error", message=f"LLM Error: {e.message}")
            return

        response_content = "".join(text_parts)
        tool_calls = native_tool_calls or _extract_tool_calls(response_content)

        if not tool_calls:
            if response_content:
                yield AgentEvent(kind="done", content=response_content, tool_log=tool_log)
                return
            # Model said nothing at all — surface why instead of a bare fallback.
            msg = stream_error or "模型未返回任何内容，请检查 API 配置或模型可用性。"
            logger.warning("LLM returned empty content and no tool calls (%s)", msg)
            yield AgentEvent(kind="error", message=msg)
            return

        # ---- 2. Announce tool calls before executing ------------------
        parsed: list[dict | None] = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = None  # non-object arguments cannot be **unpacked
            except json.JSONDecodeError as exc:
                logger.warning("Tool call arguments not valid JSON: %s", exc)
                args = None
            parsed.append(args)
            yield AgentEvent(
                kind="tool_call",
                call_id=tc.get("id", ""),
                name=tc["function"]["name"],
                args=args or {},
            )

        msgs.append(
            {
                "role": "assistant",
                "content": response_content,
                "tool_calls": tool_calls,
            }
        )

        # ---- 3. Execute: unsafe tools sequentially, safe ones in parallel
        results: list[ToolResult] = [None] * len(tool_calls)  # type: ignore[list-item]
        durations = [0] * len(tool_calls)

        async def _run(i: int) -> None:
            tc = tool_calls[i]
            name = tc["function"]["name"]
            start = time.perf_counter()
            if parsed[i] is None:
                result = ToolResult(f"Invalid tool arguments for {name}", success=False)
            else:
                result = await _execute_tool_call(name, parsed[i])
            results[i] = result
            durations[i] = int((time.perf_counter() - start) * 1000)

        sequential = [
            i
            for i, tc in enumerate(tool_calls)
            if not _tool_is_safe(tc["function"]["name"])
        ]
        parallel = [
            i
            for i, tc in enumerate(tool_calls)
            if _tool_is_safe(tc["function"]["name"])
        ]

        for i in sequential:
            await _run(i)
            yield _tool_result_event(tool_calls[i], results[i], durations[i])

        if parallel:
            # Results are yielded as each finishes, not in call order.
            for fut in asyncio.as_completed([asyncio.create_task(_run(i)) for i in parallel]):
                await fut  # _run stores into results[]; events emitted below
            for i in parallel:
                yield _tool_result_event(tool_calls[i], results[i], durations[i])

        # ---- 4. Feed results back in the model's original order -------
        for i, tc in enumerate(tool_calls):
            result = results[i]
            tool_log.append(
                {
                    "tool": tc["function"]["name"],
                    "args": parsed[i] or {},
                    "result": result.content,
                    "success": result.success,
                }
            )
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _sanitize_tool_content(result.content, tc["function"]["name"]),
                }
            )

    # Max rounds reached
    last_content = next(
        (m.get("content") for m in reversed(msgs) if m.get("role") == "assistant" and m.get("content")),
        "",
    )
    yield AgentEvent(
        kind="done",
        content=last_content or "已达最大工具调用轮数限制。",
        tool_log=tool_log,
    )


def _tool_is_safe(name: str) -> bool:
    """Return True if the named built-in tool is marked concurrency-safe."""
    tool: BaseTool | None = registry.get(name)
    return bool(tool is not None and getattr(tool, "concurrency_safe", False))


def _tool_result_event(tc: dict, result: ToolResult, duration_ms: int) -> AgentEvent:
    return AgentEvent(
        kind="tool_result",
        call_id=tc.get("id", ""),
        name=tc["function"]["name"],
        result=result.content,
        success=result.success,
        duration_ms=duration_ms,
        metadata=dict(result.metadata or {}),
    )


async def _execute_tool_call(tool_name: str, tool_args: dict) -> ToolResult:
    """Execute one tool call against the built-in or plugin registry."""
    tool = registry.get(tool_name)
    if tool is not None:
        try:
            return await tool.execute(**tool_args)
        except Exception as e:
            return ToolResult(f"Tool error: {str(e)}", success=False)

    plugin_name = plugin_registry.find_command_plugin(tool_name)
    if plugin_name is not None:
        try:
            return await plugin_registry.execute_command(plugin_name, tool_name, tool_args)
        except Exception as e:
            return ToolResult(f"Plugin tool error: {str(e)}", success=False)

    return ToolResult(f"Unknown tool: {tool_name}", success=False)


# ------------------------------------------------------------------
# Legacy blocking wrapper
# ------------------------------------------------------------------


async def agent_chat(messages: list[dict]) -> tuple[str, list[dict]]:
    """Run an agent chat loop with tool support (blocking variant).

    Drains :func:`agent_chat_stream` and returns
    ``(final_response_text, tool_calls_log)``. Kept for existing callers;
    LLM failures are returned as an error *string* to match the historical
    contract rather than raising.
    """
    final_text = ""
    tool_log: list[dict] = []

    async for event in agent_chat_stream(messages):
        if event.kind == "done":
            final_text, tool_log = event.content, event.tool_log
        elif event.kind == "error":
            final_text, tool_log = event.message, event.tool_log

    return final_text, tool_log


# Models that skip native function calling emit the tool call as literal JSON
# in the message body:  {"tool": "calculator", "args": {"expression": "1+1"}}
# ``_extract_tool_calls`` recovers a real tool call from it, but the same
# characters were already streamed as text. Strip them before displaying or
# persisting the answer.
_EMBEDDED_TOOL_JSON_RE = re.compile(
    r'\{"tool"\s*:\s*"[\w.-]+"\s*,\s*"args"\s*:\s*\{(?:[^{}]|(?:\{[^{}]*\}))*\s*\}\s*\}',
)


def strip_tool_call_json(text: str) -> str:
    """Remove ``{"tool":..., "args":{...}}`` fragments from assistant text."""
    if not text or '"tool"' not in text:
        return text
    return _EMBEDDED_TOOL_JSON_RE.sub("", text)


def _sanitize_tool_content(content: str, tool_name: str) -> str:
    """Prepare tool result content for the LLM context window.

    Removes embedded base64 image data and truncates overly long outputs
    to avoid LLM API payload/size errors. The full result is still kept
    in the tool log for frontend display.
    """
    if not content:
        return content

    # Strip embedded base64 image URIs (screenshots, browser screenshots)
    sanitized = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[image data omitted]", content)

    # Cap length sent to the LLM; keep the beginning (description + OCR text)
    max_len = 8000
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + f"\n... [truncated from {len(sanitized)} chars]"

    return sanitized


def _extract_tool_calls(response_content: str) -> list[dict]:
    """Extract tool calls from LLM response.

    The system prompt instructs the LLM to output tool calls as JSON:
    {"tool": "name", "args": {...}}
    """
    tool_calls: list[dict] = []

    # Look for JSON tool call patterns — use a more robust regex that handles
    # nested braces by matching balanced braces
    pattern = r'\{"tool"\s*:\s*"(\w+)"\s*,\s*"args"\s*:\s*(\{(?:[^{}]|(?:\{[^{}]*\}))*\})\s*\}'
    matches = re.findall(pattern, response_content)

    for i, (name, args_str) in enumerate(matches):
        try:
            args = json.loads(args_str)
            tool_calls.append(
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
        except json.JSONDecodeError:
            # Fallback: try to find JSON objects in the broader response
            pass

    # Fallback: try to find any {"tool": ...} JSON in the text
    if not tool_calls:
        # Find the last occurrence of {"tool" and try to parse from there
        idx = response_content.rfind('{"tool"')
        if idx >= 0:
            # Find the matching closing brace
            depth = 0
            for j in range(idx, len(response_content)):
                if response_content[j] == '{':
                    depth += 1
                elif response_content[j] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(response_content[idx:j+1])
                            if "tool" in obj and "args" in obj:
                                tool_calls.append({
                                    "id": "call_0",
                                    "type": "function",
                                    "function": {
                                        "name": obj["tool"],
                                        "arguments": json.dumps(obj["args"]),
                                    },
                                })
                        except json.JSONDecodeError:
                            pass
                        break

    return tool_calls
