from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import database as db_mod
from main import app as _app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def tmp_db(monkeypatch, tmp_path):
    """把 DB_PATH 指到临时文件，并接管全局连接，用完还原。"""
    path = str(tmp_path / "test_jarvis.db")
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    saved_conn = db_mod._db
    db_mod._db = None
    yield path
    if db_mod._db is not None:
        await db_mod._db.close()
    db_mod._db = saved_conn


@pytest.fixture()
async def client(tmp_db):
    await db_mod.init_db()
    async with AsyncClient(
        transport=ASGITransport(app=_app, raise_app_exceptions=True),
        base_url="http://127.0.0.1:18200",
    ) as c:
        yield c


@pytest.mark.anyio
async def test_privacy_stats_has_human_readable_size(client):
    """前端 PrivacyTab 依赖 database_size_human，不能改回只有 db_size_mb。"""
    resp = await client.get("/api/privacy/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["conversation_count"], int)
    assert isinstance(data["message_count"], int)
    assert isinstance(data["plugin_count"], int)
    assert isinstance(data["database_size_bytes"], int)
    assert isinstance(data["database_size_human"], str)
    assert data["database_size_human"]  # 非空字符串
