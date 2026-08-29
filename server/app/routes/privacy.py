"""JARVIS data-privacy routes — export, purge, and statistics.

Phase 6.3 — Allows users to download or delete all their personal data
stored by the server, complying with GDPR-like requirements.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import database as db

logger = logging.getLogger("jarvis")

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


# ------------------------------------------------------------------
# GET /api/privacy/stats — data statistics
# ------------------------------------------------------------------


@router.get("/stats")
async def privacy_stats() -> dict[str, Any]:
    """Return statistics about stored user data.

    Includes conversation count, message count, plugin count,
    and estimated database file size.
    """
    database = await db._get_db()

    # Conversation count
    cursor = await database.execute("SELECT COUNT(*) FROM conversations")
    row = await cursor.fetchone()
    conversation_count: int = row[0] if row else 0

    # Message count
    cursor = await database.execute("SELECT COUNT(*) FROM messages")
    row = await cursor.fetchone()
    message_count: int = row[0] if row else 0

    # Plugin count
    cursor = await database.execute("SELECT COUNT(*) FROM plugin_settings")
    row = await cursor.fetchone()
    plugin_count: int = row[0] if row else 0

    # Estimated DB size (file size on disk)
    db_size_bytes = 0
    try:
        db_size_bytes = os.path.getsize(db.DB_PATH)
    except OSError:
        pass

    return {
        "conversation_count": conversation_count,
        "message_count": message_count,
        "plugin_count": plugin_count,
        "database_size_bytes": db_size_bytes,
        "database_size_human": _human_size(db_size_bytes),
    }


# ------------------------------------------------------------------
# GET /api/privacy/export — export all user data as JSON
# ------------------------------------------------------------------


@router.get("/export")
async def privacy_export() -> JSONResponse:
    """Export all user data as a downloadable JSON file.

    Includes conversations with messages, settings, and plugin configs.
    Returns the response with ``Content-Disposition`` so the browser
    triggers a file download.
    """
    database = await db._get_db()

    # Conversations + messages
    conversations_rows = await database.execute_fetchall(
        "SELECT * FROM conversations ORDER BY updated_at DESC"
    )
    export_data: dict[str, Any] = {"conversations": []}

    for conv_row in conversations_rows:
        conv = dict(conv_row)
        cid = conv["id"]
        messages_rows = await database.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (cid,),
        )
        conv["messages"] = [dict(m) for m in messages_rows]
        export_data["conversations"].append(conv)

    # Settings
    settings_rows = await database.execute_fetchall(
        "SELECT key, value FROM settings ORDER BY key"
    )
    export_data["settings"] = [dict(s) for s in settings_rows]

    # Plugin configs
    plugin_rows = await database.execute_fetchall(
        "SELECT plugin_name, enabled, config, updated_at FROM plugin_settings ORDER BY plugin_name"
    )
    # Parse JSON config strings back into dicts for cleaner export
    for p in plugin_rows:
        p = dict(p)
        if p.get("config"):
            try:
                p["config"] = json.loads(p["config"])
            except (json.JSONDecodeError, TypeError):
                pass
    export_data["plugin_configs"] = [dict(p) for p in plugin_rows]

    payload = json.dumps(export_data, indent=2, ensure_ascii=False, default=str)

    return JSONResponse(
        content=payload,
        status_code=200,
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=jarvis_data_export.json"
        },
    )


# ------------------------------------------------------------------
# DELETE /api/privacy/purge — delete all user data except config
# ------------------------------------------------------------------


@router.delete("/purge")
async def privacy_purge() -> dict[str, Any]:
    """Delete all user-generated data while preserving core settings.

    Clears: conversations, messages, and plugin_settings.
    Preserves: the ``settings`` table (model config, persona, etc.).

    Returns a summary of what was removed.
    """
    database = await db._get_db()

    # Count before deletion for confirmation
    cursor = await database.execute("SELECT COUNT(*) FROM conversations")
    conv_count = (await cursor.fetchone())[0]

    cursor = await database.execute("SELECT COUNT(*) FROM messages")
    msg_count = (await cursor.fetchone())[0]

    cursor = await database.execute("SELECT COUNT(*) FROM plugin_settings")
    plugin_count = (await cursor.fetchone())[0]

    # Execute deletions (conversations cascade to messages via FK)
    await database.execute("DELETE FROM plugin_settings")
    await database.execute("DELETE FROM conversations")
    await database.commit()

    logger.info(
        "Privacy purge completed: %d conversations, %d messages, %d plugin configs removed",
        conv_count,
        msg_count,
        plugin_count,
    )

    return {
        "status": "ok",
        "message": "All user data purged successfully. Core settings preserved.",
        "removed": {
            "conversations": conv_count,
            "messages": msg_count,
            "plugin_configs": plugin_count,
        },
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string (e.g. ``"1.23 MB"``)."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    remaining = float(size_bytes)
    while remaining >= 1024 and idx < len(units) - 1:
        remaining /= 1024
        idx += 1
    return f"{remaining:.2f} {units[idx]}"
