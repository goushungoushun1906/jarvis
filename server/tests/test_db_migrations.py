"""SQLite 建表与增量迁移测试。

每个用例都跑在独立的临时 db 文件上，绝不碰开发库 `server/jarvis.db`。
重点验证「老库升级」这条路径：新列必须被加上、存量数据要被回填、
而且**重复执行不能报错或重复加列**。
"""

from __future__ import annotations

import os
import tempfile

import aiosqlite
import pytest

from app import database as db_mod


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


async def _columns(path: str, table: str) -> list[str]:
    async with aiosqlite.connect(path) as conn:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in await cur.fetchall()]


async def _tables(path: str) -> set[str]:
    async with aiosqlite.connect(path) as conn:
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in await cur.fetchall()}


# ---------------------------------------------------------------------------
# 全新库建表
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_init_db_creates_expected_tables(tmp_db):
    await db_mod.init_db()
    tables = await _tables(tmp_db)
    for expected in (
        "conversations", "messages", "settings", "memories",
        "memories_fts", "plugin_settings", "llm_calls",
    ):
        assert expected in tables, f"缺少表 {expected}"


@pytest.mark.anyio
async def test_init_db_applies_migrations_on_fresh_db(tmp_db):
    await db_mod.init_db()
    assert "parent_id" in await _columns(tmp_db, "conversations")
    assert "forked_from_message_id" in await _columns(tmp_db, "conversations")
    assert "confidence" in await _columns(tmp_db, "memories")


@pytest.mark.anyio
async def test_init_db_is_idempotent(tmp_db):
    """重复初始化不能报错，也不能把 schema 搞乱。"""
    await db_mod.init_db()
    before = await _columns(tmp_db, "conversations")
    await db_mod.close_db()
    await db_mod.init_db()
    assert await _columns(tmp_db, "conversations") == before


# ---------------------------------------------------------------------------
# 老库升级
# ---------------------------------------------------------------------------

_LEGACY_SCHEMA = """
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE memories (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    content               TEXT NOT NULL,
    category              TEXT DEFAULT 'general',
    importance            REAL DEFAULT 0.5,
    source_conversation_id TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count          INTEGER DEFAULT 0,
    last_accessed         TIMESTAMP
);
"""


@pytest.fixture
async def legacy_db(tmp_db):
    """造一个"迁移前"的库：conversations 没有 parent_id/forked_from_message_id。

    同时把连接挂到 db_mod._db 上——`_run_migrations` 是通过共享连接执行的。
    """
    async with aiosqlite.connect(tmp_db) as conn:
        await conn.executescript(_LEGACY_SCHEMA)
        await conn.execute(
            "INSERT INTO memories (content, category, importance) VALUES (?, ?, ?)",
            ("老记忆一条", "fact", 0.9),
        )
        await conn.commit()

    db_mod._db = await aiosqlite.connect(tmp_db)
    db_mod._db.row_factory = aiosqlite.Row
    return tmp_db


@pytest.mark.anyio
async def test_migration_adds_missing_conversation_columns(legacy_db):
    assert "parent_id" not in await _columns(legacy_db, "conversations")
    await db_mod._run_migrations()
    cols = await _columns(legacy_db, "conversations")
    assert "parent_id" in cols
    assert "forked_from_message_id" in cols


@pytest.mark.anyio
async def test_migration_adds_confidence_and_backfills(legacy_db):
    await db_mod._run_migrations()
    assert "confidence" in await _columns(legacy_db, "memories")
    async with aiosqlite.connect(legacy_db) as conn:
        cur = await conn.execute("SELECT confidence FROM memories WHERE content = ?", ("老记忆一条",))
        row = await cur.fetchone()
    # 存量行不能留 NULL，否则后续 housekeeping 的比较会失效
    assert row[0] == 0.5


@pytest.mark.anyio
async def test_migration_is_idempotent(legacy_db):
    await db_mod._run_migrations()
    first = await _columns(legacy_db, "conversations")
    await db_mod._run_migrations()
    assert await _columns(legacy_db, "conversations") == first


@pytest.mark.anyio
async def test_migration_preserves_existing_rows(legacy_db):
    await db_mod._run_migrations()
    async with aiosqlite.connect(legacy_db) as conn:
        cur = await conn.execute("SELECT content, importance FROM memories")
        rows = await cur.fetchall()
    assert rows == [("老记忆一条", 0.9)]


@pytest.mark.anyio
async def test_full_init_on_legacy_db_upgrades_in_place(legacy_db):
    """端到端：老库文件直接跑 init_db 要能升级到完整 schema。"""
    await db_mod.init_db()
    tables = await _tables(legacy_db)
    # 迁移补列 + 建表补缺的表
    assert {"conversations", "messages", "llm_calls", "plugin_settings"} <= tables
    cols = await _columns(legacy_db, "conversations")
    assert "parent_id" in cols and "forked_from_message_id" in cols


# ---------------------------------------------------------------------------
# 连接生命周期
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_db_raises_before_init(monkeypatch):
    monkeypatch.setattr(db_mod, "_db", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        await db_mod._get_db()


@pytest.mark.anyio
async def test_close_db_is_safe_when_not_initialised(monkeypatch):
    monkeypatch.setattr(db_mod, "_db", None)
    await db_mod.close_db()  # 不应抛异常
    assert db_mod._db is None
