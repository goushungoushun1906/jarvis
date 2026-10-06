"""Model router tests — tier 选择、降级链、凭证规范化。

重点是不与真实数据库/网关耦合：``get_model_profiles`` 全部走 monkeypatch，
这样降级链的各种组合都能被确定性地测到。
"""

from __future__ import annotations

import pytest

from app import model_router as mr


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def _profile(pid: str, tier: str, model: str = None, base: str = "https://x/v1",
             key: str = "sk-test", active: bool = False) -> dict:
    return {
        "id": pid,
        "name": pid,
        "tier": tier,
        "model": model or pid,
        "base_url": base,
        "api_key": key,
        "is_active": active,
    }


class _ProfileStore(list):
    """list 的子类，只多一个 `set()`——用它当 fixture 的返回值。"""

    def set(self, items: list[dict]) -> None:
        self.clear()
        self.extend(items)


@pytest.fixture
def profiles(monkeypatch) -> _ProfileStore:
    """替换 get_model_profiles；用例通过 `profiles.set([...])` 注入数据。"""
    store: _ProfileStore = _ProfileStore()

    async def fake() -> list[dict]:
        return list(store)

    monkeypatch.setattr(mr, "get_model_profiles", fake)
    return store


# ---------------------------------------------------------------------------
# select_tier
# ---------------------------------------------------------------------------


def _msgs(text: str, n: int = 1) -> list[dict]:
    return [{"role": "user", "content": text} for _ in range(n)]


def test_select_tier_explicit_overrides_heuristics():
    assert mr.select_tier(_msgs("hi"), explicit_tier="deep") == "deep"
    assert mr.select_tier(_msgs("hi"), explicit_tier="fast") == "fast"


def test_select_tier_fast_for_short_greeting():
    assert mr.select_tier(_msgs("你好")) == "fast"


def test_select_tier_mid_when_tools_present():
    assert mr.select_tier(_msgs("hi"), tools=[{"type": "function"}]) == "mid"


def test_select_tier_mid_on_complex_keywords():
    assert mr.select_tier(_msgs("帮我分析一下这段代码")) == "mid"


def test_select_tier_mid_when_attachments_present():
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]
    assert mr.select_tier(msgs) == "mid"


def test_select_tier_deep_on_long_content():
    assert mr.select_tier(_msgs("x" * 2500)) == "deep"


def test_select_tier_deep_on_long_history():
    assert mr.select_tier(_msgs("hi", n=15)) == "deep"


def test_select_tier_handles_multimodal_content_length():
    """content 是 list 时要能算出长度，不能直接当 0 处理。"""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "y" * 2100}]}]
    assert mr.select_tier(msgs) == "deep"


def test_select_tier_detects_legacy_attachment_json():
    """历史库里存的是 JSON 字符串而不是 list 分片。"""
    msgs = [{"role": "user", "content": '{"images": ["a.png"], "text": "看图"}'}]
    assert mr.select_tier(msgs) == "mid"


def test_select_tier_ignores_unparseable_legacy_payload():
    msgs = [{"role": "user", "content": "{not json at all"}]
    assert mr.select_tier(msgs) == "fast"


# ---------------------------------------------------------------------------
# resolve_profile_chain — 降级链
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_chain_starts_with_requested_tier(profiles):
    profiles.set([_profile("f", "fast"), _profile("m", "mid"), _profile("d", "deep")])
    chain = await mr.resolve_profile_chain("fast")
    assert [p["id"] for p in chain] == ["f", "m", "d"]


@pytest.mark.anyio
async def test_chain_falls_through_mid_then_deep_then_fast(profiles):
    profiles.set([_profile("f", "fast"), _profile("m", "mid"), _profile("d", "deep")])
    assert [p["id"] for p in await mr.resolve_profile_chain("mid")] == ["m", "d", "f"]
    assert [p["id"] for p in await mr.resolve_profile_chain("deep")] == ["d", "m", "f"]


@pytest.mark.anyio
async def test_chain_dedupes_when_only_one_profile(profiles):
    """只有一个 fast 档时，链里不能出现三个重复的同一条。"""
    profiles.set([_profile("only", "fast")])
    assert [p["id"] for p in await mr.resolve_profile_chain("deep")] == ["only"]


@pytest.mark.anyio
async def test_chain_appends_active_profile_as_last_resort(profiles):
    profiles.set([
        _profile("f", "fast"),
        _profile("m", "mid"),
        _profile("custom", "custom", active=True),
    ])
    chain = await mr.resolve_profile_chain("fast")
    assert [p["id"] for p in chain] == ["f", "m", "custom"]


@pytest.mark.anyio
async def test_chain_skips_active_if_already_present(profiles):
    profiles.set([_profile("f", "fast", active=True), _profile("m", "mid")])
    assert [p["id"] for p in await mr.resolve_profile_chain("fast")] == ["f", "m"]


@pytest.mark.anyio
async def test_chain_empty_when_no_profiles(profiles):
    profiles.set([])
    assert await mr.resolve_profile_chain("fast") == []


# ---------------------------------------------------------------------------
# resolve_profile
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_profile_returns_head_of_chain(profiles):
    profiles.set([_profile("m", "mid"), _profile("d", "deep")])
    assert (await mr.resolve_profile("mid"))["id"] == "m"


@pytest.mark.anyio
async def test_resolve_profile_raises_when_nothing_configured(profiles):
    profiles.set([])
    with pytest.raises(RuntimeError, match="No model profile"):
        await mr.resolve_profile("fast")


# ---------------------------------------------------------------------------
# profile_to_api_credentials
# ---------------------------------------------------------------------------


def test_credentials_use_profile_base_url_and_model():
    p = _profile("x", "mid", model="gpt-4o", base="https://api.openai.com/v1")
    base, key, model = mr.profile_to_api_credentials(p)
    assert base == "https://api.openai.com/v1"
    assert key == "sk-test"
    assert model == "gpt-4o"


def test_credentials_normalize_marketing_model_name():
    """设置里填 DeepSeek-V4.1-Flash 时，网关只认 deepseek-flash。"""
    p = _profile("x", "mid", model="DeepSeek-V4.1-Flash")
    _, _, model = mr.profile_to_api_credentials(p)
    assert model == "deepseek-flash"


def test_credentials_fall_back_to_global_key_when_profile_has_none(monkeypatch):
    monkeypatch.setattr(mr.settings, "llm_api_key", "sk-global")
    p = _profile("x", "mid")
    p["api_key"] = ""
    _, key, _ = mr.profile_to_api_credentials(p)
    assert key == "sk-global"


# ---------------------------------------------------------------------------
# get_profile_tier / apply_profile_to_settings
# ---------------------------------------------------------------------------


def test_get_profile_tier_defaults_to_mid_for_unknown():
    assert mr.get_profile_tier("totally-custom") == "mid"


def test_apply_profile_to_settings(monkeypatch):
    mr.apply_profile_to_settings(
        _profile("p", "mid", model="m1", base="https://b/v1", key="sk-1")
    )
    assert mr.settings.llm_model == "m1"
    assert mr.settings.llm_base_url == "https://b/v1"
    assert mr.settings.llm_api_key == "sk-1"


def test_apply_profile_keeps_existing_key_when_profile_has_none(monkeypatch):
    monkeypatch.setattr(mr.settings, "llm_api_key", "sk-existing")
    p = _profile("p", "mid")
    p["api_key"] = ""
    mr.apply_profile_to_settings(p)
    assert mr.settings.llm_api_key == "sk-existing"
