"""Tests for the auto-update check route (Phase 6.4)."""

from __future__ import annotations

import pytest

from app.routes import update as update_mod


@pytest.fixture(scope="session")
def anyio_backend():
    """项目用 anyio（而非 pytest-asyncio）跑异步用例，见 tests/test_api.py。"""
    return "asyncio"


# ---------------------------------------------------------------------------
# parse_version / _compare_versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", (1, 2, 3, "")),
        ("v1.2.3", (1, 2, 3, "")),
        ("V2.0.0", (2, 0, 0, "")),
        ("  1.0.0  ", (1, 0, 0, "")),
        ("1.2.3-beta.1", (1, 2, 3, "beta.1")),
        ("0.0.0", (0, 0, 0, "")),
        # 非法输入必须退化成 0.0.0 而不是抛异常
        ("", (0, 0, 0, "")),
        (None, (0, 0, 0, "")),
        ("not-a-version", (0, 0, 0, "")),
        ("abc.def", (0, 0, 0, "")),
    ],
)
def test_parse_version(raw, expected):
    assert update_mod.parse_version(raw) == expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.0", "1.0.1", -1),
        ("1.0.1", "1.0.0", 1),
        ("1.0.0", "1.0.0", 0),
        ("1.9.0", "1.10.0", -1),  # 数字比较，不是字符串比较
        ("2.0.0", "10.0.0", -1),
        ("1.2.0-beta", "1.2.0", -1),  # 预发布低于正式版
        ("1.2.0", "1.2.0-beta", 1),
        ("1.2.0-alpha", "1.2.0-beta", -1),
        ("garbage", "1.0.0", -1),  # 非法版本当 0.0.0
    ],
)
def test_compare_versions(a, b, expected):
    assert update_mod._compare_versions(a, b) == expected


def test_latest_local_version_picks_max():
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {
            "1.0.0": "a",
            "1.10.0": "b",
            "1.9.0": "c",
        }
        # 必须是 1.10.0 而不是字符串序最大的 "1.9.0"
        assert update_mod._latest_local_version() == "1.10.0"
    finally:
        update_mod.RELEASE_NOTES = original


def test_latest_local_version_empty_registry():
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {}
        assert update_mod._latest_local_version() == "0.0.0"
    finally:
        update_mod.RELEASE_NOTES = original


# ---------------------------------------------------------------------------
# check_update endpoint behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_update_offline_no_update_when_current_is_latest(monkeypatch):
    """未配置 manifest 且当前版本已是最新 → update_available=False。"""
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "")
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {"1.0.0": "Initial release"}
        resp = await update_mod.check_update(current="1.0.0")
        assert resp["update_available"] is False
        assert resp["latest_version"] == "1.0.0"
        assert resp["source"] == "local"
        assert resp["release_notes"] == "Initial release"
    finally:
        update_mod.RELEASE_NOTES = original


@pytest.mark.anyio
async def test_check_update_offline_detects_newer(monkeypatch):
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "")
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {"1.0.0": "a", "1.2.0": "b"}
        resp = await update_mod.check_update(current="1.0.0")
        assert resp["update_available"] is True
        assert resp["latest_version"] == "1.2.0"
        assert resp["release_notes"] == "b"
    finally:
        update_mod.RELEASE_NOTES = original


@pytest.mark.anyio
async def test_check_update_dev_build_newer_than_registry_not_flagged(monkeypatch):
    """回归：旧实现用 current != latest，本地开发版比注册表新时会误报"有更新"。"""
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "")
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {"1.0.0": "a"}
        resp = await update_mod.check_update(current="2.0.0-dev")
        assert resp["update_available"] is False
    finally:
        update_mod.RELEASE_NOTES = original


@pytest.mark.anyio
async def test_check_update_uses_remote_manifest(monkeypatch):
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "https://example.com/m.json")
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {"1.0.0": "old"}

        async def fake_fetch():
            return {
                "version": "1.5.0",
                "notes": "remote notes",
                "download_url": "https://example.com/setup.exe",
            }

        monkeypatch.setattr(update_mod, "_fetch_remote_manifest", fake_fetch)
        resp = await update_mod.check_update(current="1.0.0")
        assert resp["update_available"] is True
        assert resp["latest_version"] == "1.5.0"
        assert resp["release_notes"] == "remote notes"
        assert resp["download_url"] == "https://example.com/setup.exe"
        assert resp["source"] == "remote"
    finally:
        update_mod.RELEASE_NOTES = original


@pytest.mark.anyio
async def test_check_update_remote_older_than_local_keeps_local(monkeypatch):
    """manifest 回退（远端比本地旧）时不能拿远端覆盖本地。"""
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "https://example.com/m.json")
    original = update_mod.RELEASE_NOTES
    try:
        update_mod.RELEASE_NOTES = {"2.0.0": "local"}

        async def fake_fetch():
            return {"version": "1.0.0", "notes": "remote old"}

        monkeypatch.setattr(update_mod, "_fetch_remote_manifest", fake_fetch)
        resp = await update_mod.check_update(current="1.0.0")
        assert resp["latest_version"] == "2.0.0"
        assert resp["release_notes"] == "local"
        assert resp["update_available"] is True
    finally:
        update_mod.RELEASE_NOTES = original


@pytest.mark.anyio
async def test_fetch_remote_manifest_degrades_on_error(monkeypatch):
    """远端不可达必须返回 None 而不是把异常抛到接口上。"""

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "https://example.com/m.json")
    monkeypatch.setattr(update_mod.httpx, "AsyncClient", lambda **kw: _Boom())
    assert await update_mod._fetch_remote_manifest() is None


@pytest.mark.anyio
async def test_fetch_remote_manifest_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(update_mod.settings, "update_manifest_url", "")
    assert await update_mod._fetch_remote_manifest() is None
