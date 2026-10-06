"""MCP client tests — connect-failure handling.

Run with::

    ./.venv/Scripts/python.exe -m pytest tests/test_mcp_client.py -v

Regression guard: ``connect()`` used to call ``disconnect()`` from inside its
own ``async with self._lock`` block. ``asyncio.Lock`` is not reentrant, so any
MCP server that failed to launch deadlocked the whole backend startup.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)
_libs_dir = os.path.join(_server_root, "libs")
if os.path.isdir(_libs_dir) and _libs_dir not in sys.path:
    sys.path.insert(0, _libs_dir)

from app.mcp_client import MCPServerConfig, MCPServerConnection  # noqa: E402


def _unreachable_sse_config() -> MCPServerConfig:
    # Port 1 on loopback refuses connections immediately on every platform.
    return MCPServerConfig(
        name="dead",
        transport="sse",
        url="http://127.0.0.1:1/sse",
        enabled=True,
    )


def test_connect_failure_returns_instead_of_deadlocking():
    conn = MCPServerConnection(_unreachable_sse_config())

    async def _attempt() -> bool:
        return await asyncio.wait_for(conn.connect(), timeout=15)

    start = time.perf_counter()
    ok = asyncio.run(_attempt())
    elapsed = time.perf_counter() - start

    assert ok is False
    assert elapsed < 14, "connect() must not hang while holding its own lock"
    assert conn.session is None
    assert conn.tools == []


def test_disconnect_is_idempotent():
    conn = MCPServerConnection(_unreachable_sse_config())

    async def _cycle() -> None:
        await conn.connect()
        await asyncio.wait_for(conn.disconnect(), timeout=10)
        await asyncio.wait_for(conn.disconnect(), timeout=10)

    asyncio.run(_cycle())

    assert conn.session is None


def test_connect_failure_does_not_block_a_later_connect():
    """The lock must be released even on the failure path."""
    conn = MCPServerConnection(_unreachable_sse_config())

    async def _twice() -> tuple[bool, bool]:
        first = await asyncio.wait_for(conn.connect(), timeout=15)
        second = await asyncio.wait_for(conn.connect(), timeout=15)
        return first, second

    assert asyncio.run(_twice()) == (False, False)


def test_stdio_without_command_fails_fast():
    conn = MCPServerConnection(
        MCPServerConfig(name="nocmd", transport="stdio", command=None, enabled=True)
    )

    assert asyncio.run(asyncio.wait_for(conn.connect(), timeout=10)) is False


def test_unknown_transport_fails_fast():
    conn = MCPServerConnection(
        MCPServerConfig(name="weird", transport="carrier-pigeon", enabled=True)
    )

    assert asyncio.run(asyncio.wait_for(conn.connect(), timeout=10)) is False
