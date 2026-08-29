"""MCP (Model Context Protocol) client integration for JARVIS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger("jarvis")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_servers.json")


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    enabled: bool = True


@dataclass
class MCPTool:
    """Lightweight representation of an MCP tool."""

    server_name: str
    name: str
    description: str
    parameters_schema: dict[str, Any]


class MCPServerConnection:
    """Manages a single MCP server connection and its discovered tools."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.session: ClientSession | None = None
        self.tools: list[MCPTool] = []
        self._exit_stack = AsyncExitStack()
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Establish connection to the MCP server and list tools."""
        async with self._lock:
            if self.session is not None:
                return True

            try:
                if self.config.transport == "stdio":
                    if not self.config.command:
                        logger.error("MCP server %s missing command", self.config.name)
                        return False

                    server_params = StdioServerParameters(
                        command=self.config.command,
                        args=self.config.args or [],
                        env={**os.environ, **(self.config.env or {})},
                    )
                    read, write = await self._exit_stack.enter_async_context(
                        stdio_client(server_params)
                    )
                elif self.config.transport == "sse":
                    if not self.config.url:
                        logger.error("MCP server %s missing URL", self.config.name)
                        return False
                    read, write = await self._exit_stack.enter_async_context(
                        sse_client(self.config.url)
                    )
                else:
                    logger.error("Unknown MCP transport: %s", self.config.transport)
                    return False

                session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self.session = session

                # Discover tools
                tools_response = await session.list_tools()
                self.tools = []
                for tool in tools_response.tools:
                    # Handle both old SDK (input_schema) and new SDK (inputSchema)
                    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object"}
                    self.tools.append(
                        MCPTool(
                            server_name=self.config.name,
                            name=tool.name,
                            description=tool.description or "",
                            parameters_schema=schema,
                        )
                    )

                logger.info(
                    "MCP server '%s' connected with %d tools",
                    self.config.name,
                    len(self.tools),
                )
                return True

            except Exception as e:
                logger.error("Failed to connect MCP server '%s': %s", self.config.name, e)
                await self.disconnect()
                return False

    async def disconnect(self) -> None:
        """Close the connection."""
        async with self._lock:
            self.session = None
            self.tools = []
            await self._exit_stack.aclose()
            self._exit_stack = AsyncExitStack()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this server."""
        if self.session is None:
            return f"MCP server '{self.config.name}' is not connected"

        try:
            result = await self.session.call_tool(tool_name, arguments)

            # Convert content to string
            parts: list[str] = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                else:
                    parts.append(str(content))
            return "\n".join(parts)

        except Exception as e:
            logger.error("MCP tool call error (%s/%s): %s", self.config.name, tool_name, e)
            return f"Error calling MCP tool {tool_name}: {e}"


class MCPClient:
    """Central manager for all configured MCP servers."""

    def __init__(self):
        self._servers: dict[str, MCPServerConnection] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    def load_config(self, path: str = CONFIG_PATH) -> None:
        """Load MCP server configurations from JSON file."""
        self._configs = {}

        if not os.path.exists(path):
            logger.warning("MCP config not found at %s, using empty config", path)
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for name, cfg in data.get("servers", {}).items():
                self._configs[name] = MCPServerConfig(
                    name=name,
                    transport=cfg.get("transport", "stdio"),
                    command=cfg.get("command"),
                    args=cfg.get("args", []),
                    url=cfg.get("url"),
                    env=cfg.get("env"),
                    enabled=cfg.get("enabled", True),
                )

            logger.info("Loaded %d MCP server configs", len(self._configs))

        except Exception as e:
            logger.error("Failed to load MCP config: %s", e)

    async def initialize(self) -> None:
        """Connect to all enabled MCP servers."""
        for name, config in self._configs.items():
            if not config.enabled:
                continue
            conn = MCPServerConnection(config)
            success = await conn.connect()
            if success:
                self._servers[name] = conn

    async def shutdown(self) -> None:
        """Disconnect all MCP servers."""
        for conn in self._servers.values():
            await conn.disconnect()
        self._servers.clear()

    def reload(self) -> asyncio.Future:
        """Reload configuration and reconnect (async)."""
        # Return a coroutine so caller can await it
        return self._reload()

    async def _reload(self) -> None:
        await self.shutdown()
        self.load_config()
        await self.initialize()

    def list_servers(self) -> list[dict[str, Any]]:
        """Return status of all configured servers."""
        result = []
        for name, config in self._configs.items():
            conn = self._servers.get(name)
            result.append(
                {
                    "name": name,
                    "enabled": config.enabled,
                    "connected": conn is not None and conn.session is not None,
                    "tool_count": len(conn.tools) if conn else 0,
                    "transport": config.transport,
                }
            )
        return result

    def list_tools(self) -> list[MCPTool]:
        """Return all tools from connected servers."""
        tools: list[MCPTool] = []
        for conn in self._servers.values():
            tools.extend(conn.tools)
        return tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on a specific server."""
        conn = self._servers.get(server_name)
        if conn is None:
            return f"MCP server '{server_name}' is not connected"
        return await conn.call_tool(tool_name, arguments)

    async def call_tool_by_name(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by its global name (searches connected servers)."""
        for conn in self._servers.values():
            for tool in conn.tools:
                if tool.name == tool_name:
                    return await conn.call_tool(tool_name, arguments)
        return f"MCP tool '{tool_name}' not found on any connected server"


# Global singleton
mcp_client = MCPClient()
