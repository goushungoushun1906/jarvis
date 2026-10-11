"""Per-request auth token for the local backend.

Why this exists when the backend already checks Origin and Host
--------------------------------------------------------------
Both of those checks are about *where a request came from*, and neither can
tell our own app apart from anything else running on the same machine:

* Any page the user visits can POST to ``http://127.0.0.1:18200``. It needs no
  credentials, and CORS deliberately allows loopback origins.
* A local HTML file opened in a browser gets ``Origin: null`` — which is
  exactly what the packaged Electron app sends, so we cannot reject it without
  breaking ourselves.

So "it came from loopback" is not an identity. The token is: it is a secret
that only the installed app can read, and it never enters the renderer at all
(Electron attaches it at the network layer, see ``electron/main.js``). A page
in the user's browser cannot read the file, so it cannot forge the header.

Rollout safety
--------------
Enforcement is on for packaged builds (``sys.frozen``) and off in dev/tests,
because flipping it on everywhere would 401 every existing unit test and every
``curl`` the user has bookmarked. Two env vars make that explicit:

* ``JARVIS_REQUIRE_API_TOKEN=1`` — force it on (used by the tests here).
* ``JARVIS_DISABLE_API_TOKEN=1`` — force it off (escape hatch if a build
  somehow cannot read the token file; a wrong guess here locks the user out of
  their own app, which is worse than the threat).
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys
from pathlib import Path

TOKEN_HEADER = "x-jarvis-token"
TOKEN_ENV = "JARVIS_API_TOKEN"
_REQUIRE_ENV = "JARVIS_REQUIRE_API_TOKEN"
_DISABLE_ENV = "JARVIS_DISABLE_API_TOKEN"

# Must stay reachable without the token: the app probes /health to decide
# whether to start a backend at all, so gating it would deadlock startup.
# Nothing sensitive is exposed by these.
PUBLIC_PATH_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def token_dir() -> Path:
    """Where the token lives — must be computable by Electron too.

    Electron reads the same path with ``process.env.APPDATA`` / ``os.homedir()``,
    so the two sides agree without any IPC.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "JARVIS"
    return Path.home() / ".jarvis"


def token_path() -> Path:
    return token_dir() / "api_token.txt"


def load_or_create_token() -> str:
    """Read the token, creating (and persisting) one on first run.

    Never raises: a backend that cannot start because of an unreadable token
    file is strictly worse than a backend without a token. Callers that get an
    empty string simply do not enforce.
    """
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    try:
        path = token_path()
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="utf-8")
        # Best effort: the file is in the user's own profile, but tighten it
        # anyway so another account on the machine cannot read it.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return token
    except OSError:
        return ""


def is_enabled() -> bool:
    """Should requests be gated right now?"""
    if _env_flag(_DISABLE_ENV):
        return False
    if _env_flag(_REQUIRE_ENV):
        return True
    return bool(getattr(sys, "frozen", False))


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant-time comparison — a timing oracle on the header is real."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.strip(), expected)


def is_public_path(path: str) -> bool:
    return path.startswith(PUBLIC_PATH_PREFIXES)
