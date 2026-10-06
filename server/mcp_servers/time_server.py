"""Simple MCP time server for JARVIS.

使用 mcp >= 2.0 的回调式 API（旧版的 `@app.list_tools()` 装饰器在 2.0 里
已移除，用会导致 Server 启动时 AttributeError，客户端表现为 "Connection closed"）。
"""
from __future__ import annotations

import asyncio
import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

_NO_ARGS_SCHEMA: dict = {"type": "object", "properties": {}}

_TOOLS = [
    Tool(
        name="get_current_time",
        description=(
            "Return the current date and time. Includes local time (with UTC offset "
            "and timezone name) and the UTC time in ISO 8601 format."
        ),
        input_schema=_NO_ARGS_SCHEMA,
    ),
    Tool(
        name="get_timestamp",
        description="Return the current Unix timestamp in seconds.",
        input_schema=_NO_ARGS_SCHEMA,
    ),
]


async def _list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=list(_TOOLS))


def _local_now() -> tuple[datetime.datetime, str]:
    """Return (aware local datetime, timezone name)."""
    local = datetime.datetime.now().astimezone()
    return local, (local.tzname() or "local")


async def _call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
    name = params.name

    if name == "get_current_time":
        local, tzname = _local_now()
        utc = datetime.datetime.now(datetime.timezone.utc)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"Local time: {local.isoformat()} ({tzname})\n"
                        f"UTC time: {utc.isoformat()}"
                    ),
                )
            ]
        )

    if name == "get_timestamp":
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        return CallToolResult(
            content=[TextContent(type="text", text=f"Current Unix timestamp: {ts}")]
        )

    return CallToolResult(
        content=[TextContent(type="text", text=f"Unknown tool: {name}")],
        is_error=True,
    )


app = Server(
    "jarvis-time-server",
    version="1.0.0",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
