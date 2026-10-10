"""Tests for the MCP configuration API used by the settings panel.

重点不是 CRUD 本身，而是几个容易出错的边界：

- 打包版必须读写 exe 同级的配置文件，而不是打进包的副本 —— 否则用户改完
  重启就回到旧配置（和 .env 当初踩的是同一个坑）。
- 写盘必须原子化：写坏 mcp_servers.json 会导致 MCP 再也起不来。
- 配置里将来可能出现未知字段，必须在写入时收敛，不能让配置文件变成
  任意命令的载体。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# 与 test_api.py 保持一致：把 server/ 与 libs/ 放进 sys.path，这样从 server
# 目录直接跑单文件时也能 import。
_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

_libs_dir = os.path.join(_server_root, "libs")
if os.path.isdir(_libs_dir) and _libs_dir not in sys.path:
    sys.path.insert(0, _libs_dir)

os.environ.setdefault("JARVIS_MODE", "dev")

from app import mcp_client as mcp_module  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def app():
    from main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    # loopback 而非 httpx 默认的 "testserver"：A6 之后后端拒绝非 loopback 的
    # Host，测试必须和真实客户端一样走 127.0.0.1。
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://127.0.0.1:18200"
    ) as ac:
        yield ac


@pytest.fixture
def mcp():
    return mcp_module.mcp_client


# ─── 字段收敛 ────────────────────────────────────────────────────────


def test_sanitize_drops_unknown_keys():
    cleaned = mcp_module._sanitize_server_config(
        {
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "evil_injected": "rm -rf /",
        }
    )
    assert "evil_injected" not in cleaned
    assert cleaned["command"] == "python"
    assert cleaned["args"] == ["server.py"]


def test_sanitize_normalizes_types():
    cleaned = mcp_module._sanitize_server_config(
        {"transport": "weird", "args": [1, 2], "enabled": "yes"}
    )
    assert cleaned["transport"] == "stdio"  # 未知 transport 回落
    assert cleaned["args"] == ["1", "2"]
    assert cleaned["enabled"] is True


def test_read_raw_config_missing_file(tmp_path, mcp):
    """文件不存在要返回空骨架，不能抛 —— 否则设置面板连"添加"表单都渲染不出来。"""
    assert mcp.read_raw_config(str(tmp_path / "nope.json")) == {"servers": {}}


# ─── 读写 ────────────────────────────────────────────────────────────


def test_write_raw_config_atomic_and_valid(tmp_path, mcp):
    path = tmp_path / "mcp_servers.json"
    mcp.write_raw_config(
        {"servers": {"demo": {"transport": "stdio", "command": "python"}}},
        str(path),
    )
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["servers"]["demo"]["command"] == "python"
    # 临时文件必须已被替换掉，不能残留
    assert not (tmp_path / "mcp_servers.json.tmp").exists()


def test_write_raw_config_rejects_bad_shape(tmp_path, mcp):
    with pytest.raises(ValueError):
        mcp.write_raw_config({"servers": "not-a-dict"}, str(tmp_path / "x.json"))


def test_write_raw_config_rejects_blank_name(tmp_path, mcp):
    with pytest.raises(ValueError):
        mcp.write_raw_config(
            {"servers": {"   ": {"command": "python"}}}, str(tmp_path / "x.json")
        )


# ─── 路由 ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_mcp_config_returns_path_and_servers(client: AsyncClient):
    resp = await client.get("/api/tools/mcp/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "path" in body
    assert "servers" in body["config"]


@pytest.mark.anyio
async def test_delete_unknown_server_is_404(client: AsyncClient):
    resp = await client.delete("/api/tools/mcp/servers/definitely-not-here")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_list_mcp_tools_is_json_array(client: AsyncClient):
    resp = await client.get("/api/tools/mcp/tools")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_put_mcp_config_rejects_non_object_servers(client: AsyncClient):
    resp = await client.put("/api/tools/mcp/config", json={"servers": "nope"})
    assert resp.status_code in (400, 422)
