"""密钥存储：凭据管理器优先于 .env，且失败必须显式报错。

要锁住的行为：
1. 平台不支持时全部静默降级，绝不能让后端起不来；
2. 存/删之后 secret_sources() 必须反映真实来源 —— 用户是靠它判断"到底读的
   是哪一份"，返回陈旧值会让"我存了但还是 401"变成无解的猜谜；
3. 端点只认白名单内的字段名，写失败要报错而不是静默吞掉。
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

os.environ.setdefault("JARVIS_MODE", "dev")

from main import app as _app  # noqa: E402
from app import config as config_module  # noqa: E402
from app import secrets as secrets_module  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(_app, base_url="http://127.0.0.1:18200") as c:
        yield c


# ── 平台不支持时必须降级，不能崩 ──────────────────────────────────────

def test_get_returns_none_when_unsupported(monkeypatch):
    monkeypatch.setattr(secrets_module, "_SUPPORTED", False)
    assert secrets_module.get_secret("llm_api_key") is None


def test_set_raises_when_unsupported(monkeypatch):
    monkeypatch.setattr(secrets_module, "_SUPPORTED", False)
    with pytest.raises(RuntimeError):
        secrets_module.set_secret("llm_api_key", "x")


def test_delete_returns_false_when_unsupported(monkeypatch):
    monkeypatch.setattr(secrets_module, "_SUPPORTED", False)
    assert secrets_module.delete_secret("llm_api_key") is False


# ── 真实往返（仅 Windows 本机） ───────────────────────────────────────

def test_roundtrip_store_read_delete():
    if not secrets_module.is_supported():
        pytest.skip("非 Windows 平台，跳过凭据管理器往返测试")

    name = f"pytest-{uuid.uuid4().hex}"
    try:
        assert secrets_module.get_secret(name) is None
        secrets_module.set_secret(name, "sk-测试-1234")
        assert secrets_module.get_secret(name) == "sk-测试-1234"
    finally:
        secrets_module.delete_secret(name)
    assert secrets_module.get_secret(name) is None


def test_set_rejects_empty_value():
    if not secrets_module.is_supported():
        pytest.skip("非 Windows 平台")
    with pytest.raises(ValueError):
        secrets_module.set_secret("pytest-empty", "")


# ── 来源解析 ──────────────────────────────────────────────────────────

def test_source_reports_keychain_when_stored(monkeypatch):
    original = config_module.settings.llm_api_key
    monkeypatch.setattr(secrets_module, "get_secret", lambda _n: "from-keychain")
    try:
        sources = config_module.reload_secrets()
        assert sources["llm_api_key"] == "keychain"
        assert config_module.settings.llm_api_key == "from-keychain"
    finally:
        config_module.reload_secrets()
        object.__setattr__(config_module.settings, "llm_api_key", original)


def test_source_falls_back_to_env_when_not_stored(monkeypatch):
    monkeypatch.setattr(secrets_module, "get_secret", lambda _n: None)
    sources = config_module.reload_secrets()
    assert sources["llm_api_key"] in ("env", "missing")


# ── 端点 ──────────────────────────────────────────────────────────────

def test_list_secrets_exposes_support_and_sources(client):
    body = client.get("/api/config/secrets").json()
    assert "supported" in body
    assert "llm_api_key" in body["sources"]


def test_save_rejects_unmanaged_name(client):
    resp = client.post("/api/config/secrets/evil_key", json={"value": "x"})
    assert resp.status_code == 404


def test_save_rejects_blank_value(client):
    resp = client.post("/api/config/secrets/llm_api_key", json={"value": "   "})
    assert resp.status_code == 400
