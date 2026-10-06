"""JARVIS Auto-Update Check API (Phase 6.4).

Version source of truth, in priority order:

1. ``settings.update_manifest_url`` — 远端 manifest（GitHub Release / CDN JSON）。
   配置了就联网拉；拉不到（超时 / 5xx / 非法 JSON）静默降级到第 2 项。
2. 本地 ``RELEASE_NOTES`` 注册表 —— 离线兜底，随代码发布一起更新。

manifest 期望的 JSON 形状（字段全部可选，缺了就退回本地值）::

    {
      "version": "1.2.0",
      "notes": "修复 xxx，新增 yyy",
      "download_url": "https://example.com/JARVIS-1.2.0-Setup.exe"
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, Query

from ..config import settings

logger = logging.getLogger("jarvis.update")

router = APIRouter(prefix="/api/update", tags=["update"])

# ---------------------------------------------------------------------------
# Release notes registry — 离线兜底，发版时记得同步这里
# ---------------------------------------------------------------------------
RELEASE_NOTES: dict[str, str] = {
    "1.0.0": "Initial release",
}

# 只取开头那一段形如 1.2.3 / 1.2.3-beta.1 的部分，忽略后面的构建元数据
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.-]+))?")


def parse_version(version: str) -> tuple[int, int, int, str]:
    """把版本号解析成可比较的元组。

    非法输入一律当成 ``(0, 0, 0, "")``，保证比较不会抛异常——客户端传什么
    都不应该让 /api/update/check 500。
    """
    if not version:
        return (0, 0, 0, "")
    m = _VERSION_RE.match(version.strip().lstrip("vV"))
    if not m:
        return (0, 0, 0, "")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = (m.group(4) or "").lower()
    # 预发布版本（1.2.0-beta）低于正式版（1.2.0）
    return (major, minor, patch, "" if not pre else pre)


def _compare_versions(a: str, b: str) -> int:
    """返回 -1 / 0 / 1。预发布标识符用简单的字符串序（够用，不追求完整 semver）。"""
    ta, tb = parse_version(a), parse_version(b)
    if ta[:3] != tb[:3]:
        return -1 if ta[:3] < tb[:3] else 1
    pa, pb = ta[3], tb[3]
    if pa == pb:
        return 0
    if not pa:  # a 是正式版，b 是预发布 → a 更大
        return 1
    if not pb:
        return -1
    return -1 if pa < pb else 1


def _latest_local_version() -> str:
    """从本地注册表里挑出最大的版本号。"""
    if not RELEASE_NOTES:
        return "0.0.0"
    return max(RELEASE_NOTES.keys(), key=parse_version)


async def _fetch_remote_manifest() -> dict[str, Any] | None:
    """拉远端 manifest，任何异常都吞掉并返回 None（离线降级）。"""
    url = (settings.update_manifest_url or "").strip()
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.update_check_timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 更新检查不能影响主流程
        logger.warning("Update manifest fetch failed (%s): %s", url, exc)
        return None
    return data if isinstance(data, dict) else None


@router.get("/check")
async def check_update(
    current: str = Query(..., description="Current client version, e.g. 1.0.0"),
) -> dict[str, Any]:
    """Return whether a newer version is available.

    未配置 ``update_manifest_url`` 时完全离线，只按本地 ``RELEASE_NOTES`` 判断。
    """
    latest_version = _latest_local_version()
    release_notes = RELEASE_NOTES.get(latest_version, "")
    download_url = ""
    source = "local"

    manifest = await _fetch_remote_manifest()
    if manifest:
        remote_version = str(manifest.get("version") or "").strip()
        if remote_version:
            # 远端与本地谁新就用谁，避免 manifest 回退时误报"有更新"
            if _compare_versions(remote_version, latest_version) > 0:
                latest_version = remote_version
                release_notes = str(manifest.get("notes") or "")
            source = "remote"
        download_url = str(manifest.get("download_url") or "")

    update_available = _compare_versions(latest_version, current) > 0

    response: dict[str, Any] = {
        "current_version": current,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_notes": release_notes,
        "download_url": download_url,
        # 让前端/排障时知道这次判断是联网还是离线得出的
        "source": source,
    }

    logger.info(
        "Update check: current=%s latest=%s available=%s source=%s",
        current,
        latest_version,
        update_available,
        source,
    )
    return response
