"""MCP (Model Context Protocol) client integration for JARVIS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger("jarvis")


def _resolve_config_path() -> str:
    """Resolve the MCP servers config file location.

    打包（sys.frozen）后 __file__ 指向 PyInstaller 的 _MEIPASS 临时目录，
    该目录每次启动都会重建 —— 直接用它定位配置，用户改了 exe 同级的
    mcp_servers.json 也不会生效（与 .env 当初踩的是同一个坑）。
    因此 frozen 下优先 exe 同级目录（持久且可编辑），不存在时回退打包进去的那份。
    """
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "mcp_servers.json"
        if candidate.exists():
            return str(candidate)
    return str(Path(__file__).resolve().parent.parent / "mcp_servers.json")


CONFIG_PATH = _resolve_config_path()

# 只允许这些键进入配置文件：写配置是给设置面板用的，多余字段（尤其是未知的、
# 将来可能被当成命令执行的键）一律丢弃，避免配置文件变成任意命令载体。
_ALLOWED_CONFIG_KEYS = {"transport", "command", "args", "url", "env", "enabled", "description"}


def _sanitize_server_config(raw: dict) -> dict:
    """只保留已知字段，并把类型收敛到安全形状。"""
    clean: dict[str, Any] = {}

    transport = raw.get("transport", "stdio")
    clean["transport"] = transport if transport in ("stdio", "sse") else "stdio"

    for key in ("command", "url", "description"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            clean[key] = value.strip()

    args = raw.get("args")
    if isinstance(args, list):
        clean["args"] = [str(a) for a in args]

    env = raw.get("env")
    if isinstance(env, dict):
        clean["env"] = {str(k): str(v) for k, v in env.items()}

    clean["enabled"] = bool(raw.get("enabled", True))
    return clean


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
                # NOTE: must not call ``disconnect()`` here — it re-acquires
                # ``self._lock``, which this task already holds, and
                # ``asyncio.Lock`` is not reentrant. Doing so deadlocked the
                # whole backend startup whenever an MCP server failed to launch.
                await self._close()
                return False

    async def _close(self) -> None:
        """Release connection resources.

        Caller must already hold ``self._lock``.
        """
        self.session = None
        self.tools = []
        await self._exit_stack.aclose()
        self._exit_stack = AsyncExitStack()

    async def disconnect(self) -> None:
        """Close the connection."""
        async with self._lock:
            await self._close()

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

    def read_raw_config(self, path: str | None = None) -> dict:
        """返回配置文件的原始内容（含未启用的 server）。

        设置面板需要展示 command/args/url 这些细节，而 list_servers() 只给
        连接状态，所以这里把文件原样读出来。文件不存在时给空骨架，让前端
        仍能正常渲染"添加服务器"表单。

        path 在调用时解析（而不是写进默认参数），测试才能指向临时文件。
        """
        path = path or CONFIG_PATH
        if not os.path.exists(path):
            return {"servers": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to read MCP config: %s", e)
            raise ValueError(f"配置文件读取失败: {e}")

        if not isinstance(data, dict) or not isinstance(data.get("servers", {}), dict):
            raise ValueError("配置文件格式错误：需要 {'servers': {...}}")
        return data

    def write_raw_config(self, data: dict, path: str | None = None) -> None:
        """校验并原子写回配置文件。

        先写同目录临时文件再替换：直接 truncate 写的话，一旦中途抛异常
        （序列化失败、磁盘写满）就会留下半个文件，用户再也启动不了 MCP。
        """
        path = path or CONFIG_PATH
        servers = data.get("servers")
        if not isinstance(servers, dict):
            raise ValueError("配置格式错误：servers 必须是对象")

        clean_servers: dict[str, dict] = {}
        for name, raw in servers.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("服务器名称不能为空")
            if not isinstance(raw, dict):
                raise ValueError(f"服务器 '{name}' 的配置必须是对象")
            clean_servers[name.strip()] = _sanitize_server_config(raw)

        payload = {"servers": clean_servers}
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)

        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        logger.info("MCP config written to %s (%d servers)", path, len(clean_servers))

    async def apply_config(self, data: dict) -> list[dict[str, Any]]:
        """写回配置并立刻重连，返回最新服务器状态。"""
        self.write_raw_config(data)
        await self._reload()
        return self.list_servers()

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

    def list_tools_info(self) -> list[dict[str, Any]]:
        """列出所有已连接 MCP 服务器提供的工具（设置面板展示"装了什么"）。"""
        return [
            {
                "server": tool.server_name,
                "name": tool.name,
                "description": tool.description,
            }
            for tool in self.list_tools()
        ]

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
