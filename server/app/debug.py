"""JARVIS debug middleware, environment configuration, and debug info endpoint.

Phase 6.5 — Development tooling for request profiling and server introspection.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("jarvis")

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class EnvironmentConfig:
    """Runtime environment configuration for the JARVIS server.

    Reads ``JARVIS_MODE`` from environment to decide dev vs production mode.
    """

    mode: str = "dev"  # "dev" or "prod"
    debug: bool = True
    log_level: LogLevel = LogLevel.DEBUG

    # Server version (bumped manually)
    server_version: str = "1.0.0"

    # Uptime bookkeeping
    _start_time: float = field(default_factory=time.time, init=False, repr=False)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def uptime_seconds(self) -> float:
        """Return the number of seconds since the server started."""
        return round(time.time() - self._start_time, 2)

    @classmethod
    def from_env(cls) -> EnvironmentConfig:
        """Build configuration from environment variables."""
        mode = os.environ.get("JARVIS_MODE", "dev").strip().lower()
        if mode not in ("dev", "prod"):
            mode = "dev"

        debug = mode == "dev"
        log_level = LogLevel.DEBUG if debug else LogLevel.INFO

        return cls(mode=mode, debug=debug, log_level=log_level)


# Singleton — created once when the module is imported
env_config = EnvironmentConfig.from_env()


# ---------------------------------------------------------------------------
# Debug middleware
# ---------------------------------------------------------------------------


class DebugMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that adds request timing and structured logging."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        start = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"

        logger.info(
            "method=%s path=%s status=%d duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )

        return response


debug_middleware = DebugMiddleware


# ---------------------------------------------------------------------------
# Debug info router
# ---------------------------------------------------------------------------

debug_router = APIRouter(prefix="/api/debug", tags=["debug"])


@debug_router.get("/info")
async def debug_info() -> dict[str, Any]:
    """Return server diagnostic information.

    Includes version, uptime, Python version, loaded plugins count,
    database size, and active connections count.
    """
    import glob
    import sqlite3

    from app.database import DB_PATH

    # ---- Database size ----
    db_size_bytes = 0
    try:
        # Sum main DB + WAL + SHM
        for candidate in glob.glob(f"{DB_PATH}*"):
            db_size_bytes += os.path.getsize(candidate)
    except OSError:
        pass

    # ---- Active connections (approximate: count SQLite locks) ----
    active_connections = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("PRAGMA busy_timeout")
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        active_connections = cursor.fetchone()[0]   # table count as proxy
        conn.close()
    except Exception:
        active_connections = -1  # unavailable

    # ---- Loaded plugins count ----
    plugins_count = 0
    try:
        from app.plugins.manager import manager as plugin_manager
        plugins_count = len(plugin_manager.get_all_plugin_info())
    except Exception:
        pass

    return {
        "server_version": env_config.server_version,
        "mode": env_config.mode,
        "debug": env_config.debug,
        "uptime_seconds": env_config.uptime_seconds,
        "python_version": sys.version,
        "loaded_plugins_count": plugins_count,
        "database_size_bytes": db_size_bytes,
        "database_size_human": _human_size(db_size_bytes),
        "active_connections_count": active_connections,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
