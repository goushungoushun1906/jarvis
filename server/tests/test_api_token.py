"""per-request API token。

Origin 与 Host 校验只能判断"请求来自 loopback"，分不清是我们自己的应用还是
用户浏览器里的任意网页——两者都可能呈现 Origin: null。token 才是身份：它由
后端生成、只有本机已安装的应用读得到，且从不进入渲染进程（Electron 在网络层
注入），所以网页既读不到也伪造不了。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import api_token
from app.security import ApiTokenGuard


@pytest.fixture()
def token_env(monkeypatch):
    """固定 token 与开关，避免测试互相影响真实的用户目录。"""

    def _set(token: str, require: bool = True) -> None:
        monkeypatch.setenv(api_token.TOKEN_ENV, token)
        if require:
            monkeypatch.setenv(api_token._REQUIRE_ENV, "1")
        else:
            monkeypatch.delenv(api_token._REQUIRE_ENV, raising=False)
        monkeypatch.delenv(api_token._DISABLE_ENV, raising=False)

    return _set


def _app_with_guard(token: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiTokenGuard, token=token)

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    return app


@pytest.mark.anyio
async def test_request_without_token_is_rejected(token_env):
    token_env("secret-token")
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_guard("secret-token")),
        base_url="http://127.0.0.1:18200",
    ) as c:
        resp = await c.get("/api/ping")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_request_with_token_is_allowed(token_env):
    token_env("secret-token")
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_guard("secret-token")),
        base_url="http://127.0.0.1:18200",
    ) as c:
        resp = await c.get("/api/ping", headers={"X-Jarvis-Token": "secret-token"})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_wrong_token_is_rejected(token_env):
    token_env("secret-token")
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_guard("secret-token")),
        base_url="http://127.0.0.1:18200",
    ) as c:
        resp = await c.get("/api/ping", headers={"X-Jarvis-Token": "guess"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_health_stays_reachable_without_token(token_env):
    """应用靠 /health 判断要不要起后端，把它也拦了会直接死锁。"""
    token_env("secret-token")
    app = _app_with_guard("secret-token")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:18200"
    ) as c:
        assert (await c.get("/health")).status_code == 200


@pytest.mark.anyio
async def test_preflight_is_not_gated(token_env):
    """带自定义头的浏览器请求必须先 OPTIONS 预检，预检不带 token。"""
    token_env("secret-token")
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_guard("secret-token")),
        base_url="http://127.0.0.1:18200",
    ) as c:
        resp = await c.options(
            "/api/ping", headers={"Access-Control-Request-Headers": "x-jarvis-token"}
        )
    assert resp.status_code != 401


@pytest.mark.anyio
async def test_disabled_by_env_kill_switch(monkeypatch):
    """逃生开关：关掉之后就不校验，避免猜错把用户锁在自己应用外面。"""
    monkeypatch.setenv(api_token.TOKEN_ENV, "secret-token")
    monkeypatch.setenv(api_token._DISABLE_ENV, "1")
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_guard("secret-token")),
        base_url="http://127.0.0.1:18200",
    ) as c:
        assert (await c.get("/api/ping")).status_code == 200


# ---------------------------------------------------------------------------
# token 本身
# ---------------------------------------------------------------------------


def test_token_file_is_created_once_and_reused(monkeypatch, tmp_path):
    monkeypatch.delenv(api_token.TOKEN_ENV, raising=False)
    monkeypatch.setattr(api_token, "token_dir", lambda: tmp_path)

    first = api_token.load_or_create_token()
    assert first
    assert api_token.token_path().exists()

    second = api_token.load_or_create_token()
    assert second == first  # 重启后仍是同一个，否则前端会突然 401


def test_token_matches_is_constant_time():
    assert api_token.token_matches("abc", "abc") is True
    assert api_token.token_matches("abd", "abc") is False
    assert api_token.token_matches("", "abc") is False
    assert api_token.token_matches(None, "abc") is False


def test_public_paths():
    assert api_token.is_public_path("/health") is True
    assert api_token.is_public_path("/api/chat") is False


def test_not_enabled_in_dev_by_default(monkeypatch):
    """dev / 测试默认不校验 —— 否则现有几百个用例全会 401。"""
    monkeypatch.delenv(api_token._REQUIRE_ENV, raising=False)
    monkeypatch.delenv(api_token._DISABLE_ENV, raising=False)
    monkeypatch.setattr(api_token.sys, "frozen", False, raising=False)
    assert api_token.is_enabled() is False


def test_enabled_in_packaged_build(monkeypatch):
    monkeypatch.delenv(api_token._DISABLE_ENV, raising=False)
    monkeypatch.delenv(api_token._REQUIRE_ENV, raising=False)
    monkeypatch.setattr(api_token.sys, "frozen", True, raising=False)
    assert api_token.is_enabled() is True
