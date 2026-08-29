"""JARVIS SQLite database layer using aiosqlite."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .config import settings
from .models import DEFAULT_TIER_MAP
from . import vector_memory

logger = logging.getLogger("jarvis")

# Database file path — one level up from this file (server directory).
# In PyInstaller production, __file__ points to the temp extraction dir;
# use the resolved path stored by config.py, or fall back to exe parent.
import sys as _sys
if getattr(_sys, 'frozen', False):
    _meipass = getattr(_sys, '_MEIPASS', None)
    if _meipass:
        _fallback_server_dir = str(Path(_meipass))
    else:
        _fallback_server_dir = str(Path(_sys.executable).parent)
else:
    _fallback_server_dir = None
_DB_DIR = os.environ.get('_JARVIS_SERVER_DIR', _fallback_server_dir)
DB_PATH = os.path.join(_DB_DIR or os.path.dirname(os.path.dirname(__file__)), "jarvis.db")

_db: aiosqlite.Connection | None = None


async def _get_db() -> aiosqlite.Connection:
    """Return the shared connection, raising if the database is not initialised."""
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db


async def init_db() -> None:
    """Create tables if they do not exist and open a long-lived connection."""
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row

    await _db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS conversations (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conv
            ON messages(conversation_id);

        CREATE TABLE IF NOT EXISTS memories (
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

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            category,
            content='',
            tokenize='unicode61'
        );

        CREATE TABLE IF NOT EXISTS plugin_settings (
            plugin_name  TEXT PRIMARY KEY,
            enabled      INTEGER NOT NULL DEFAULT 1,
            config       TEXT,
            updated_at   TEXT NOT NULL
        );
        """)

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS llm_calls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            model         TEXT NOT NULL,
            tier          TEXT NOT NULL,
            profile_id    TEXT,
            input_chars   INTEGER DEFAULT 0,
            output_chars  INTEGER DEFAULT 0,
            duration_ms   INTEGER DEFAULT 0,
            success       INTEGER NOT NULL DEFAULT 1,
            error         TEXT,
            cost_estimate REAL DEFAULT 0.0
        );

        CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp
            ON llm_calls(timestamp);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_model
            ON llm_calls(model);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_tier
            ON llm_calls(tier);
        """)

    await _db.commit()
    await _run_migrations()


async def _run_migrations():
    """Apply incremental schema migrations."""
    db = await _get_db()

    cursor = await db.execute("PRAGMA table_info(conversations)")
    columns = [row[1] for row in await cursor.fetchall()]

    if "parent_id" not in columns:
        await db.execute("ALTER TABLE conversations ADD COLUMN parent_id TEXT REFERENCES conversations(id) ON DELETE SET NULL")

    if "forked_from_message_id" not in columns:
        await db.execute("ALTER TABLE conversations ADD COLUMN forked_from_message_id INTEGER")

    cursor = await db.execute("PRAGMA table_info(memories)")
    memory_columns = [row[1] for row in await cursor.fetchall()]

    if "confidence" not in memory_columns:
        await db.execute("ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 0.5")
        await db.execute("UPDATE memories SET confidence = 0.5 WHERE confidence IS NULL")

    await db.commit()


async def close_db() -> None:
    """Close the shared database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_conversation(
    title: str | None = None,
    parent_id: str | None = None,
    forked_from_message_id: int | None = None,
) -> dict:
    db = await _get_db()
    cid = str(uuid4())
    now = _now_iso()
    await db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at, parent_id, forked_from_message_id) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, title, now, now, parent_id, forked_from_message_id),
    )
    await db.commit()
    return {
        "id": cid,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "parent_id": parent_id,
        "forked_from_message_id": forked_from_message_id,
    }


async def list_conversations(limit: int = 50, offset: int = 0) -> list[dict]:
    db = await _get_db()
    rows = await db.execute_fetchall(
        """
        SELECT c.id,
               COALESCE(c.title,
                   (SELECT SUBSTR(m2.content, 1, 30) FROM messages m2
                    WHERE m2.conversation_id = c.id AND m2.role = 'user'
                    ORDER BY m2.id ASC LIMIT 1)
               ) AS title,
               c.created_at, c.updated_at,
               COUNT(m.id) AS message_count,
               c.parent_id,
               c.forked_from_message_id,
               c.title AS original_title
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    result = []
    for r in rows:
        conv = dict(r)
        if conv.get("title") and len(conv["title"]) >= 30 and conv.get("original_title") is None:
            conv["title"] = conv["title"] + "..."
        conv.pop("original_title", None)
        result.append(conv)
    return result


async def get_conversation(conversation_id: str) -> dict | None:
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    if not rows:
        return None
    conv = dict(rows[0])
    msg_rows = await db.execute_fetchall(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    )
    conv["messages"] = [dict(r) for r in msg_rows]
    conv["message_count"] = len(msg_rows)
    return conv


async def delete_conversation(conversation_id: str) -> bool:
    db = await _get_db()
    cursor = await db.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_conversation_title(conversation_id: str, title: str) -> bool:
    db = await _get_db()
    now = _now_iso()
    cursor = await db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conversation_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def add_message(conversation_id: str, role: str, content: str) -> dict:
    db = await _get_db()
    now = _now_iso()
    cursor = await db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, now),
    )
    await db.commit()
    mid = cursor.lastrowid
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    await db.commit()
    return {
        "id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": now,
    }


async def get_messages(conversation_id: str, limit: int | None = None) -> list[dict]:
    db = await _get_db()
    if limit is not None:
        rows = await db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows.reverse()
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        )
    return [dict(r) for r in rows]


async def get_setting(key: str) -> str | None:
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    )
    if not rows:
        return None
    return rows[0]["value"]


async def set_setting(key: str, value: str) -> None:
    db = await _get_db()
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await db.commit()


async def add_memory(
    content: str,
    category: str = "general",
    importance: float = 0.5,
    source_conversation_id: str = None,
    confidence: float = 0.5,
) -> dict:
    """Add a new memory and index it in FTS. Returns the memory dict."""
    db = await _get_db()
    now = _now_iso()
    cursor = await db.execute(
        "INSERT INTO memories (content, category, importance, confidence, source_conversation_id, created_at, updated_at)\n           VALUES (?, ?, ?, ?, ?, ?, ?)",
        (content, category, importance, confidence, source_conversation_id, now, now),
    )
    rowid = cursor.lastrowid
    await db.execute(
        "INSERT INTO memories_fts(rowid, content, category) VALUES (?, ?, ?)",
        (rowid, content, category),
    )
    await db.commit()
    try:
        await vector_memory.add_memory_vector(
            memory_id=rowid,
            content=content,
            category=category,
            importance=importance,
            source_conversation_id=source_conversation_id,
            created_at=now,
        )
    except Exception as e:
        logger.error("Failed to index memory %d in vector store: %s", rowid, e)
    return {
        "id": rowid,
        "content": content,
        "category": category,
        "importance": importance,
        "confidence": confidence,
        "source_conversation_id": source_conversation_id,
        "created_at": now,
        "updated_at": now,
        "access_count": 0,
        "last_accessed": None,
    }


def _escape_fts5_query(query: str) -> str:
    """Escape an FTS5 query by quoting each token.

    FTS5 treats characters like '-', '*', '"' as operators. Wrapping tokens in
    double quotes makes the search literal and avoids syntax errors.
    """
    tokens = query.strip().split()
    if not tokens:
        return '""'
    escaped = []
    for token in tokens:
        safe = token.replace('"', '""')
        escaped.append(f'"{safe}"')
    return " ".join(escaped)


async def search_memories(query: str, limit: int = 10) -> list[dict]:
    """Full-text search memories using FTS5."""
    db = await _get_db()
    safe_query = _escape_fts5_query(query)
    rows = await db.execute_fetchall(
        """
        SELECT m.id, m.content, m.category, m.importance,
               m.source_conversation_id, m.created_at, m.updated_at,
               m.access_count, m.last_accessed
        FROM memories_fts AS fts
        JOIN memories AS m ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (safe_query, limit),
    )
    return [dict(r) for r in rows]


async def get_memories(category: str = None, limit: int = 50, offset: int = 0) -> list[dict]:
    """Get memories, optionally filtered by category."""
    db = await _get_db()
    if category:
        rows = await db.execute_fetchall(
            "SELECT * FROM memories WHERE category = ? ORDER BY importance DESC, created_at DESC LIMIT ? OFFSET ?",
            (category, limit, offset),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [dict(r) for r in rows]


async def delete_memory(memory_id: int) -> bool:
    """Delete a memory (and its FTS/vector entries). Returns True if deleted."""
    db = await _get_db()
    await db.execute(
        "INSERT INTO memories_fts(memories_fts, rowid) VALUES('delete', ?)",
        (memory_id,),
    )
    cursor = await db.execute(
        "DELETE FROM memories WHERE id = ?",
        (memory_id,),
    )
    await db.commit()
    try:
        await vector_memory.delete_memory_vector(memory_id)
    except Exception as e:
        logger.error("Failed to delete memory %d from vector store: %s", memory_id, e)
    return cursor.rowcount > 0


async def get_memories_for_context(limit: int = 5, min_importance: float = 0.3) -> list[dict]:
    """Get important memories for injecting into conversation context.
    Returns top N memories by importance * recency score, above min_importance."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        """
        SELECT * FROM memories
        WHERE importance >= ?
        ORDER BY importance DESC, last_accessed DESC NULLS LAST, created_at DESC
        LIMIT ?
        """,
        (min_importance, limit),
    )
    return [dict(r) for r in rows]


async def update_memory_access(memory_id: int) -> None:
    """Increment access_count and update last_accessed."""
    db = await _get_db()
    now = _now_iso()
    await db.execute(
        "UPDATE memories\n           SET access_count = access_count + 1, last_accessed = ?\n           WHERE id = ?",
        (now, memory_id),
    )
    await db.commit()


_MODEL_PROFILES_KEY = "model_profiles"
_ACTIVE_PROFILE_KEY = "active_model_profile"

BUILTIN_PROFILES = [
    {"id": "agnes-flash", "name": "Agnes Flash", "model": "agnes-2.0-flash", "base_url": "https://apihub.agnes-ai.com/v1", "api_key": "", "provider": "Agnes AI", "is_active": True, "tier": DEFAULT_TIER_MAP["agnes-flash"]},
    {"id": "agnes-15", "name": "Agnes 1.5", "model": "agnes-2.0-flash", "base_url": "https://apihub.agnes-ai.com/v1", "api_key": "", "provider": "Agnes AI", "is_active": False, "tier": DEFAULT_TIER_MAP["agnes-15"]},
    {"id": "gpt4o-mini", "name": "GPT-4o Mini", "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "api_key": "", "provider": "OpenAI", "is_active": False, "tier": DEFAULT_TIER_MAP["gpt4o-mini"]},
    {"id": "gpt4o", "name": "GPT-4o", "model": "gpt-4o", "base_url": "https://api.openai.com/v1", "api_key": "", "provider": "OpenAI", "is_active": False, "tier": DEFAULT_TIER_MAP["gpt4o"]},
    {"id": "deepseek", "name": "DeepSeek V3", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "api_key": "", "provider": "DeepSeek", "is_active": False, "tier": DEFAULT_TIER_MAP["deepseek"]},
    {"id": "doubao", "name": "豆包 Pro", "model": "doubao-pro-32k", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "api_key": "", "provider": "字节豆包", "is_active": False, "tier": DEFAULT_TIER_MAP["doubao"]},
    {"id": "deepseek-r1-ollama", "name": "DeepSeek R1 8B (本地)", "model": "deepseek-r1:8b", "base_url": "http://localhost:11434/v1", "api_key": "ollama", "provider": "Ollama 本地", "is_active": False, "tier": DEFAULT_TIER_MAP["deepseek-r1-ollama"]},
    {"id": "claude", "name": "Claude Sonnet", "model": "claude-sonnet-4-20250514", "base_url": "https://api.anthropic.com/v1", "api_key": "", "provider": "Anthropic", "is_active": False, "tier": DEFAULT_TIER_MAP["claude"]},
]


async def get_model_profiles() -> list[dict]:
    """Get default models + custom models. Defaults are Agnes 2.0 and local Ollama."""
    saved = await get_setting(_MODEL_PROFILES_KEY)
    custom_profiles = json.loads(saved) if saved else []
    custom_by_id = {cp["id"]: cp for cp in custom_profiles}

    active_id = await get_setting(_ACTIVE_PROFILE_KEY) or "agnes-15"

    DEFAULT_BUILTIN_IDS = {"agnes-15", "deepseek-r1-ollama"}

    profiles = {}

    def _resolve_profile(profile_id: str) -> dict | None:
        builtin = next((b for b in BUILTIN_PROFILES if b["id"] == profile_id), None)
        custom = custom_by_id.get(profile_id)
        if custom and builtin:
            return {**builtin, **custom}
        if custom:
            return custom.copy()
        if builtin:
            return builtin.copy()
        return None

    for bid in DEFAULT_BUILTIN_IDS:
        p = _resolve_profile(bid)
        if not p:
            continue
        profiles[bid] = p

    builtin_ids = {p["id"] for p in BUILTIN_PROFILES}

    for cp in custom_profiles:
        if cp["id"] in profiles:
            continue
        builtin = next((b for b in BUILTIN_PROFILES if b["id"] == cp["id"]), None)
        if builtin:
            profiles[cp["id"]] = {**builtin, **cp}
        else:
            profiles[cp["id"]] = cp.copy()

    if active_id not in profiles:
        active_id = "agnes-15"
        await set_setting(_ACTIVE_PROFILE_KEY, active_id)

    for p in profiles.values():
        p["is_active"] = (p["id"] == active_id)
        if "tier" not in p or p["tier"] not in ("fast", "mid", "deep"):
            p["tier"] = DEFAULT_TIER_MAP.get(p["id"], "mid")
        if p.get("api_key"):
            continue
        try:
            from .config import settings
            p["api_key"] = settings.llm_api_key or ""
        except Exception:
            pass

    return list(profiles.values())


async def add_model_profile(profile: dict) -> dict:
    """Add or update a custom model profile."""
    saved = await get_setting(_MODEL_PROFILES_KEY)
    custom_profiles = json.loads(saved) if saved else []
    existing = next((p for p in custom_profiles if p["id"] == profile["id"]), None)
    if existing:
        existing.update(profile)
    else:
        custom_profiles.append(profile)
    await set_setting(_MODEL_PROFILES_KEY, json.dumps(custom_profiles))
    return profile


async def delete_model_profile(profile_id: str) -> bool:
    """Delete a custom model profile. Built-ins are not stored so nothing to delete."""
    saved = await get_setting(_MODEL_PROFILES_KEY)
    custom_profiles = json.loads(saved) if saved else []
    new_profiles = [p for p in custom_profiles if p["id"] != profile_id]
    if len(new_profiles) == len(custom_profiles):
        return False
    await set_setting(_MODEL_PROFILES_KEY, json.dumps(new_profiles))
    return True


async def switch_model_profile(profile_id: str) -> dict | None:
    """Switch to a model profile. Updates runtime settings.
    Returns the profile dict or None if not found."""
    profiles = await get_model_profiles()
    profile = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        return None

    from .config import settings
    settings.llm_model = profile["model"]
    settings.llm_base_url = profile["base_url"]
    if profile.get("api_key"):
        settings.llm_api_key = profile["api_key"]

    await set_setting(_ACTIVE_PROFILE_KEY, profile_id)

    for p in profiles:
        p["is_active"] = (p["id"] == profile_id)

    return profile


_TIER_COST_PER_1M_CHARS: dict[str, float] = {
    "fast": 0.05,
    "mid": 0.5,
    "deep": 2.0,
}


def _estimate_cost(tier: str, input_chars: int, output_chars: int) -> float:
    rate = _TIER_COST_PER_1M_CHARS.get(tier, _TIER_COST_PER_1M_CHARS["mid"])
    return rate * (input_chars + output_chars) / 1000000


async def record_llm_call(
    model: str,
    tier: str,
    profile_id: str | None,
    input_chars: int,
    output_chars: int,
    duration_ms: int,
    success: bool,
    error: str | None = None,
) -> dict:
    """Persist one LLM invocation to the cost tracking table."""
    db = await _get_db()
    now = _now_iso()
    cost = _estimate_cost(tier, input_chars, output_chars)
    cursor = await db.execute(
        "INSERT INTO llm_calls\n           (timestamp, model, tier, profile_id, input_chars, output_chars,\n            duration_ms, success, error, cost_estimate)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now, model, tier, profile_id, input_chars, output_chars, duration_ms, int(success), error or "", cost),
    )
    await db.commit()
    return {
        "id": cursor.lastrowid,
        "timestamp": now,
        "model": model,
        "tier": tier,
        "profile_id": profile_id,
        "input_chars": input_chars,
        "output_chars": output_chars,
        "duration_ms": duration_ms,
        "success": success,
        "error": error,
        "cost_estimate": cost,
    }


async def get_llm_call_summary(days: int = 30) -> dict:
    """Return aggregate usage statistics for the last N days."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT\n               COUNT(*) AS total_calls,\n               SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successful_calls,\n               SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS failed_calls,\n               SUM(input_chars) AS total_input_chars,\n               SUM(output_chars) AS total_output_chars,\n               SUM(duration_ms) AS total_duration_ms,\n               SUM(cost_estimate) AS total_cost,\n               AVG(duration_ms) AS avg_duration_ms\n           FROM llm_calls\n           WHERE timestamp >= datetime('now', '-' || ? || ' days')",
        (days,),
    )
    r = dict(rows[0]) if rows else {}
    return {
        "total_calls": r.get("total_calls") or 0,
        "successful_calls": r.get("successful_calls") or 0,
        "failed_calls": r.get("failed_calls") or 0,
        "total_input_chars": r.get("total_input_chars") or 0,
        "total_output_chars": r.get("total_output_chars") or 0,
        "total_duration_ms": r.get("total_duration_ms") or 0,
        "total_cost": round(r.get("total_cost") or 0, 6),
        "avg_duration_ms": r.get("avg_duration_ms") or 0,
    }


async def get_llm_calls(limit: int = 100, offset: int = 0, model: str | None = None, tier: str | None = None) -> list[dict]:
    """Return recent LLM call records, optionally filtered."""
    db = await _get_db()
    query = "SELECT * FROM llm_calls WHERE 1=1"
    params = []
    if model:
        query += " AND model = ?"
        params.append(model)
    if tier:
        query += " AND tier = ?"
        params.append(tier)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(query, params)
    return [dict(r) for r in rows]


async def get_llm_calls_by_model(days: int = 30) -> list[dict]:
    """Return per-model aggregated usage for the last N days."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT\n               model,\n               tier,\n               COUNT(*) AS calls,\n               SUM(input_chars) AS input_chars,\n               SUM(output_chars) AS output_chars,\n               SUM(duration_ms) AS duration_ms,\n               SUM(cost_estimate) AS cost,\n               SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successful\n           FROM llm_calls\n           WHERE timestamp >= datetime('now', '-' || ? || ' days')\n           GROUP BY model, tier\n           ORDER BY calls DESC",
        (days,),
    )
    return [dict(r) for r in rows]


async def get_llm_calls_by_day(days: int = 30) -> list[dict]:
    """Return daily call counts and cost for the last N days."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT\n               date(timestamp) AS day,\n               COUNT(*) AS calls,\n               SUM(cost_estimate) AS cost,\n               SUM(input_chars + output_chars) AS chars\n           FROM llm_calls\n           WHERE timestamp >= datetime('now', '-' || ? || ' days')\n           GROUP BY day\n           ORDER BY day DESC",
        (days,),
    )
    return [dict(r) for r in rows]


async def get_plugin_enabled_state(plugin_name: str) -> bool | None:
    """Return whether a plugin is enabled (``True``), disabled (``False``),
    or not yet recorded (``None``)."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT enabled FROM plugin_settings WHERE plugin_name = ?",
        (plugin_name,),
    )
    if not rows:
        return None
    return bool(rows[0]["enabled"])


async def set_plugin_enabled_state(plugin_name: str, enabled: bool) -> None:
    """Persist the enabled/disabled state of a plugin."""
    db = await _get_db()
    now = _now_iso()
    await db.execute(
        "INSERT INTO plugin_settings (plugin_name, enabled, updated_at)\n           VALUES (?, ?, ?)\n           ON CONFLICT(plugin_name) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
        (plugin_name, int(enabled), now),
    )
    await db.commit()


async def get_plugin_config(plugin_name: str) -> dict | None:
    """Get saved plugin configuration as a dict."""
    db = await _get_db()
    rows = await db.execute_fetchall(
        "SELECT config FROM plugin_settings WHERE plugin_name = ?",
        (plugin_name,),
    )
    if not rows or not rows[0]["config"]:
        return None
    try:
        return json.loads(rows[0]["config"])
    except json.JSONDecodeError:
        return None


async def set_plugin_config(plugin_name: str, config: dict) -> None:
    """Save plugin configuration to the database."""
    db = await _get_db()
    now = _now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    await db.execute(
        "INSERT INTO plugin_settings (plugin_name, enabled, config, updated_at)\n           VALUES (?, 1, ?, ?)\n           ON CONFLICT(plugin_name) DO UPDATE SET config = excluded.config, updated_at = excluded.updated_at",
        (plugin_name, config_str, now),
    )
    await db.commit()


async def get_all_plugin_settings() -> list[dict]:
    """Return all plugin settings rows."""
    db = await _get_db()
    rows = await db.execute_fetchall("SELECT * FROM plugin_settings ORDER BY plugin_name")
    return [dict(r) for r in rows]


async def forget_low_confidence_memories(min_confidence: float = 0.2, min_age_days: int = 30) -> int:
    """Remove low-confidence memories that have not been accessed recently.

    A memory is forgotten when:
      - confidence <= min_confidence
      - it is older than min_age_days
      - it has never been accessed or last accessed is older than min_age_days
    """
    db = await _get_db()
    cursor = await db.execute(
        "DELETE FROM memories\n           WHERE confidence <= ?\n             AND created_at <= datetime('now', '-' || ? || ' days')\n             AND (access_count = 0 OR last_accessed <= datetime('now', '-' || ? || ' days'))",
        (min_confidence, min_age_days, min_age_days),
    )
    removed = cursor.rowcount
    await db.commit()
    logger.info("Forgot %d low-confidence memories", removed)
    return removed


async def archive_stale_memories(min_age_days: int = 180) -> int:
    """Deprecate very old memories by lowering their importance.

    Returns the number of memories affected.
    """
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE memories\n           SET importance = MAX(0.1, importance * 0.5),\n               updated_at = datetime('now')\n           WHERE created_at <= datetime('now', '-' || ? || ' days')\n             AND importance > 0.1",
        (min_age_days,),
    )
    await db.commit()
    logger.info("Archived %d stale memories", cursor.rowcount)
    return cursor.rowcount
