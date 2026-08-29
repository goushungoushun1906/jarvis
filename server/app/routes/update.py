"""JARVIS Auto-Update Check API (Phase 6.4)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger("jarvis.update")

router = APIRouter(prefix="/api/update", tags=["update"])

# ---------------------------------------------------------------------------
# Release notes registry — add new entries as versions are published
# ---------------------------------------------------------------------------
RELEASE_NOTES: dict[str, str] = {
    "1.0.0": "Initial release",
}

# TODO: In production, replace the hardcoded version check below with a
#       remote lookup against a GitHub Release / CDN manifest endpoint
#       that returns the latest version, release notes, and download URL.


@router.get("/check")
async def check_update(
    current: str = Query(..., description="Current client version, e.g. 1.0.0"),
) -> dict[str, Any]:
    """Return whether a newer version is available.

    Currently returns a hardcoded response based on the
    ``RELEASE_NOTES`` mapping above.  In production this would
    query a remote release service.
    """
    latest_version: str = max(RELEASE_NOTES.keys()) if RELEASE_NOTES else "0.0.0"
    update_available: bool = current != latest_version

    response: dict[str, Any] = {
        "current_version": current,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_notes": RELEASE_NOTES.get(latest_version, ""),
        "download_url": "",  # TODO: populate with real download URL
    }

    logger.info(
        "Update check: current=%s latest=%s available=%s",
        current,
        latest_version,
        update_available,
    )
    return response
