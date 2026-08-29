"""Adapter that exposes MCP tools through the existing BaseTool framework."""

from __future__ import annotations

import logging

from .mcp_client import MCPTool, mcp_client
from .tools import BaseTool, ToolResult

logger = logging.getLogger("jarvis")


class MCPToolAdapter(BaseTool):
    """Wraps an MCP tool so it can be used like any built-in JARVIS tool."""

    def __init__(self, mcp_tool: MCPTool):
        self._mcp_tool = mcp_tool
        self.name = mcp_tool.name
        self.description = mcp_tool.description
        self.parameters_schema = mcp_tool.parameters_schema

    async def execute(self, **kwargs) -> ToolResult:
        result_text = await mcp_client.call_tool_by_name(self.name, kwargs)
        return ToolResult(result_text)


def register_mcp_tools(registry) -> None:
    """Register all currently discovered MCP tools into the tool registry.

    Should be called after ``mcp_client.initialize()``.
    """
    count = 0
    for mcp_tool in mcp_client.list_tools():
        adapter = MCPToolAdapter(mcp_tool)
        registry.register(adapter)
        count += 1
    logger.info("Registered %d MCP tools into tool registry", count)
