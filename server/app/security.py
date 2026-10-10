"""A6 — request-origin hardening for the local backend.

JARVIS binds the backend to 127.0.0.1 and ships it with no authentication,
which is a reasonable default for a single-user desktop app — but it leaves
two cheap-to-exploit local attack vectors:

1. **Drive-by from any web page.** A page the user visits can fire requests at
   ``http://127.0.0.1:18200``. No cookies or credentials are needed, so a
   simple GET/POST sails past. With ``allow_origins=["*"]`` the browser
   happily hands the response body back to that page — which means an
   arbitrary site could read conversation history, or drive tools like
   screenshot / clipboard / system control and read their output.

2. **DNS rebinding.** A hostile site resolves its own hostname to 127.0.0.1,
   so its requests arrive looking like they came from loopback. CORS alone
   cannot stop this; the Host header check can.

Both are mitigated here without introducing auth (which would mean touching
every call site in the Electron client):

* CORS is restricted to loopback origins plus ``null`` — the origin Chromium
  sends for the packaged app's ``file://`` pages.
* Every request must carry a loopback Host header.

Both guards can be disabled with an env var, because a wrong guess here locks
the user out of their own app and that is worse than the threat:

* ``JARVIS_ALLOW_ANY_ORIGIN=1`` — restore permissive CORS.
* ``JARVIS_ALLOW_ANY_HOST=1``   — skip the Host check (e.g. LAN proxying).

Deliberately *not* covered here (belongs to the full A6 work, not the minimal
fix set): per-request auth tokens, tool-call consent enforcement, and
sandboxing ``workspace_run_command``.
"""

from __future__ import annotations

import os
import re

# Loopback hosts the backend is allowed to answer for.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Loopback web origins (dev server on any port).
LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")

# What Chromium sends as Origin for pages loaded from file:// — this is how
# the packaged Electron app talks to the backend.
FILE_ORIGIN = "null"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def allow_any_origin() -> bool:
    """Kill-switch: ``JARVIS_ALLOW_ANY_ORIGIN=1`` restores permissive CORS."""
    return _env_flag("JARVIS_ALLOW_ANY_ORIGIN")


def allow_any_host() -> bool:
    """Kill-switch: ``JARVIS_ALLOW_ANY_HOST=1`` skips the Host header check."""
    return _env_flag("JARVIS_ALLOW_ANY_HOST")


def is_allowed_origin(origin: str | None) -> bool:
    """Return True if ``origin`` may read backend responses.

    ``None`` means the request carried no Origin header at all (server-to-
    server, curl, health probes). Those are not browser reads, so CORS has
    nothing to gate — allow them rather than breaking non-browser clients.
    """
    if allow_any_origin():
        return True
    if origin is None:
        return True
    origin = origin.strip()
    if not origin:
        return True
    if origin == FILE_ORIGIN:
        return True
    return bool(LOCAL_ORIGIN_RE.match(origin))


def host_of(host_header: str | None) -> str:
    """Normalise a Host header (``localhost:18200``) down to its bare host.

    Handles IPv6 literals (``[::1]:18200`` -> ``::1``) which naive
    ``split(":")`` mangles.
    """
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    return host.split(":", 1)[0]


def is_allowed_host(host_header: str | None) -> bool:
    """Return True if the request's Host header is loopback.

    An absent Host header is allowed: browsers always send one, so an attacker
    cannot exploit the gap, while some non-browser clients omit it.
    """
    if allow_any_host():
        return True
    host = host_of(host_header)
    if not host:
        return True
    return host in ALLOWED_HOSTS


def cors_kwargs() -> dict:
    """Keyword arguments for ``FastAPI.add_middleware(CORSMiddleware, ...)``.

    Kept here (rather than inline in main.py) so the shipped policy is directly
    assertable in tests without booting the whole backend.
    """
    if allow_any_origin():
        return {"allow_origins": ["*"], "allow_methods": ["*"], "allow_headers": ["*"]}
    return {
        "allow_origins": [FILE_ORIGIN],
        "allow_origin_regex": LOCAL_ORIGIN_RE.pattern,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


class LoopbackHostGuard:
    """Pure-ASGI middleware that rejects non-loopback Host headers.
    Deliberately *not* ``BaseHTTPMiddleware``: that wrapper buffers the whole
    response body, which would silently undo Phase 17's real SSE streaming by
    holding every chunk until the stream ends. Raw ASGI passes frames through
    untouched, so ``/api/chat/stream`` keeps emitting incrementally.

    Run it *outside* CORS (Starlette wraps middleware in reverse order of
    registration) so rebinding attempts are refused before any CORS work.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        host = None
        for key, value in headers:
            if key.lower() == b"host":
                try:
                    host = value.decode("latin-1")
                except UnicodeDecodeError:
                    host = None
                break

        if is_allowed_host(host):
            await self.app(scope, receive, send)
            return

        body = b'{"detail":"Host not allowed"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
