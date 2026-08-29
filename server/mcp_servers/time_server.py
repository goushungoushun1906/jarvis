"""Simple MCP time server for JARVIS."""
from __future__ import annotations

import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app = Server("jarvis-time-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_current_time",
            description="Return the current date and time in ISO 8601 format.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_timestamp",
            description="Return the current Unix timestamp in seconds.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    if name == "get_current_time":
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return [TextContent(type="text", text=f"Current UTC time: {now}")]
    if name == "get_timestamp":
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        return [TextContent(type="text", text=f"Current Unix timestamp: {ts}")]
    raise ValueError(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
