"""Agent loop: tool-aware chat with function calling."""

from __future__ import annotations

import json
import logging
import re

from .llm import call_llm, LLMError
from .tools import registry, ToolResult
from .plugins.registry import registry as plugin_registry

logger = logging.getLogger("jarvis")

MAX_TOOL_ROUNDS = 5  # Prevent infinite tool loops


async def agent_chat(messages: list[dict]) -> tuple[str, list[dict]]:
    """Run an agent chat loop with tool support.

    Merges built-in tools (from ``tools.py``) with commands from all
    enabled plugins.

    Args:
        messages: Chat messages including system prompt.

    Returns:
        Tuple of (final_response_text, tool_calls_log).
    """
    # Build combined tool list: built-in registry + enabled plugins
    tools = registry.get_openai_tools()
    plugin_tools = plugin_registry.get_all_commands()
    all_tools = tools + plugin_tools

    messages = messages.copy()
    tool_log: list[dict] = []

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            if all_tools:
                response = await call_llm(messages, stream=False, tools=all_tools)
            else:
                response = await call_llm(messages, stream=False)

            # response is now a dict: {content, tool_calls, role}
            response_content = response.get("content")
            native_tool_calls = response.get("tool_calls")

            # Check for tool calls: either native or text-extracted
            tool_calls = native_tool_calls or _extract_tool_calls(response_content or "")
            if not tool_calls:
                if response_content:
                    return response_content, tool_log
                logger.warning("LLM returned empty content and no tool calls")
                return "模型未返回任何内容，请检查 API 配置或模型可用性。", tool_log

            # Execute tool calls
            messages.append(
                {"role": "assistant", "content": response_content, "tool_calls": tool_calls}
            )

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])

                # First try built-in tool registry
                tool = registry.get(tool_name)

                if tool is not None:
                    try:
                        result = await tool.execute(**tool_args)
                    except Exception as e:
                        result = ToolResult(
                            f"Tool error: {str(e)}", success=False
                        )
                else:
                    # Try plugin registry
                    plugin_name = plugin_registry.find_command_plugin(tool_name)
                    if plugin_name is not None:
                        try:
                            result = await plugin_registry.execute_command(
                                plugin_name, tool_name, tool_args
                            )
                        except Exception as e:
                            result = ToolResult(
                                f"Plugin tool error: {str(e)}", success=False
                            )
                    else:
                        result = ToolResult(
                            f"Unknown tool: {tool_name}", success=False
                        )

                tool_log.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result.content,
                        "success": result.success,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": _sanitize_tool_content(result.content, tool_name),
                    }
                )

        except LLMError as e:
            return f"LLM Error: {e.message}", tool_log

    # Max rounds reached
    return messages[-1].get("content", "Max tool rounds reached"), tool_log


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
