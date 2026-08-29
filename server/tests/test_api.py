"""JARVIS backend API tests — Phase 6.6.

Run with::

    pytest tests/test_api.py -v

Requires ``pytest`` and ``httpx`` (installed in libs/).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Generator

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure the project root (server/) and libs/ are on sys.path so that
# imports work when running ``pytest tests/test_api.py`` from the server dir.
_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

_libs_dir = os.path.join(_server_root, "libs")
if os.path.isdir(_libs_dir) and _libs_dir not in sys.path:
    sys.path.insert(0, _libs_dir)

# Set dev mode explicitly so debug middleware is active during tests
os.environ.setdefault("JARVIS_MODE", "dev")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def app():
    """Create the FastAPI app instance with lifespan managed manually."""
    from main import app as fastapi_app

    # Trigger lifespan startup
    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """Provide an ``httpx.AsyncClient`` bound to the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "model" in data


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_config_returns_model_config(client: AsyncClient) -> None:
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "model_config" in data
    assert "persona_config" in data
    mc = data["model_config"]
    assert "model" in mc
    assert "base_url" in mc


# ---------------------------------------------------------------------------
# Plugins list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plugins_list_returns_array(client: AsyncClient) -> None:
    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Privacy stats
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_privacy_stats_returns_numbers(client: AsyncClient) -> None:
    resp = await client.get("/api/privacy/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["conversation_count"], int)
    assert isinstance(data["message_count"], int)
    assert isinstance(data["plugin_count"], int)
    assert isinstance(data["database_size_bytes"], int)


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_conversation_create_and_list(client: AsyncClient) -> None:
    # Create
    create_resp = await client.post("/api/conversations", json={"title": "Test Conv"})
    assert create_resp.status_code == 201
    conv = create_resp.json()
    conv_id = conv["id"]
    assert conv["title"] == "Test Conv"
    assert conv["message_count"] == 0

    # List
    list_resp = await client.get("/api/conversations")
    assert list_resp.status_code == 200
    convs = list_resp.json()
    assert isinstance(convs, list)
    assert any(c["id"] == conv_id for c in convs)

    # Get
    get_resp = await client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["id"] == conv_id

    # Delete
    del_resp = await client.delete(f"/api/conversations/{conv_id}")
    assert del_resp.status_code == 204


@pytest.mark.anyio
async def test_conversation_get_404(client: AsyncClient) -> None:
    resp = await client.get("/api/conversations/nonexistent-id-12345")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_conversation_delete_404(client: AsyncClient) -> None:
    resp = await client.delete("/api/conversations/nonexistent-id-12345")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Plugin enable/disable cycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plugin_enable_disable_cycle(client: AsyncClient) -> None:
    # First, get the plugins list to find an existing plugin name
    list_resp = await client.get("/api/plugins")
    assert list_resp.status_code == 200
    plugins = list_resp.json()
    if not plugins:
        pytest.skip("No plugins available to test enable/disable cycle")

    name = plugins[0]["name"]

    # Disable
    disable_resp = await client.post(f"/api/plugins/{name}/disable")
    assert disable_resp.status_code == 200
    assert disable_resp.json()["enabled"] is False

    # Enable
    enable_resp = await client.post(f"/api/plugins/{name}/enable")
    assert enable_resp.status_code == 200
    assert enable_resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# Plugin 404 for non-existent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plugin_get_404_nonexistent(client: AsyncClient) -> None:
    resp = await client.get("/api/plugins/__nonexistent_plugin_xyz__")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Plugin config — 400 for invalid key
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plugin_config_invalid_key_400(client: AsyncClient) -> None:
    """Updating a plugin with an unknown config key should return 400."""
    # Find a real plugin to test against
    list_resp = await client.get("/api/plugins")
    plugins = list_resp.json()
    if not plugins:
        pytest.skip("No plugins available to test config update")

    name = plugins[0]["name"]

    # Try to set an invalid config key
    resp = await client.put(
        f"/api/plugins/{name}/config",
        json={"__invalid_config_key_xyz__": "value"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Debug info endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_debug_info(client: AsyncClient) -> None:
    resp = await client.get("/api/debug/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "server_version" in data
    assert "mode" in data
    assert "uptime_seconds" in data
    assert "python_version" in data
    assert "loaded_plugins_count" in data
    assert "database_size_bytes" in data
