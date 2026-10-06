"""记忆抽取与召回测试。

不碰真实 SQLite / ChromaDB：``app.database`` 与 ``app.vector_memory``
全部走 monkeypatch，只验证 memory.py 自身的判定逻辑与降级行为。
"""

from __future__ import annotations

import pytest

from app import memory as mem


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# _score_confidence
# ---------------------------------------------------------------------------


def test_confidence_is_clamped_to_unit_range():
    assert 0.0 <= mem._score_confidence("我叫张三", "fact", 0.9) <= 1.0
    # 基础分已经很高，再加成也不能越过 1.0
    assert mem._score_confidence("我叫张三", "general", 0.99) <= 1.0


def test_confidence_boosts_explicit_remember_requests():
    explicit = mem._score_confidence("记住我明天要去出差", "general", 0.9)
    plain = mem._score_confidence("我明天要去出差吧", "general", 0.9)
    assert explicit > plain


def test_confidence_penalises_very_short_content():
    short = mem._score_confidence("记住啊", "general", 0.9)
    normal = mem._score_confidence("记住我常用的邮箱是 a@b.com", "general", 0.9)
    assert short < normal


def test_confidence_penalises_very_long_content():
    long = mem._score_confidence("记住" + "x" * 300, "general", 0.9)
    normal = mem._score_confidence("记住我常用的邮箱是 a@b.com", "general", 0.9)
    assert long < normal


def test_confidence_floors_at_zero():
    assert mem._score_confidence("啊", "general", 0.0) == 0.0


def test_confidence_category_ordering_event_highest():
    """只比较分类加成，所以四段内容必须等长，排除长度惩罚的干扰。"""
    same_len = "x" * 20  # 避开 <5 与 >200 两档惩罚
    scores = {
        cat: mem._score_confidence(same_len, cat, 0.5)
        for cat in ("fact", "preference", "event", "general")
    }
    assert scores["event"] > scores["fact"] > scores["preference"] > scores["general"]


# ---------------------------------------------------------------------------
# extract_memories_from_message
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_db(monkeypatch):
    """替换 database 层，记录被写入的记忆，供用例断言。"""
    created: list[dict] = []
    existing: list[dict] = []

    async def fake_search(query: str, limit: int = 10) -> list[dict]:
        return [e for e in existing if query[:20] in e["content"]][:limit]

    async def fake_add(content, category="general", importance=0.5,
                       confidence=0.5, source_conversation_id=None):
        rec = {
            "id": len(created) + 1,
            "content": content,
            "category": category,
            "importance": importance,
            "confidence": confidence,
            "source_conversation_id": source_conversation_id,
        }
        created.append(rec)
        return rec

    monkeypatch.setattr(mem.db, "search_memories", fake_search)
    monkeypatch.setattr(mem.db, "add_memory", fake_add)
    return created, existing


@pytest.mark.anyio
async def test_extract_captures_user_name(memory_db):
    created, _ = memory_db
    out = await mem.extract_memories_from_message("你好，我叫张三")
    assert len(out) == 1
    assert out[0]["category"] == "fact"
    assert "张三" in out[0]["content"]
    assert out[0]["importance"] == 0.8


@pytest.mark.anyio
async def test_extract_captures_preference(memory_db):
    created, _ = memory_db
    out = await mem.extract_memories_from_message("我喜欢用 Python 写脚本")
    assert out and out[0]["category"] == "preference"


@pytest.mark.anyio
async def test_extract_captures_explicit_remember(memory_db):
    created, _ = memory_db
    out = await mem.extract_memories_from_message("记住我周例会固定在周一上午")
    assert any(m["category"] == "general" and "记住" in m["content"] for m in out)


@pytest.mark.anyio
async def test_extract_returns_empty_for_plain_chat(memory_db):
    created, _ = memory_db
    assert await mem.extract_memories_from_message("今天天气怎么样？") == []
    assert created == []


@pytest.mark.anyio
async def test_extract_skips_duplicate_content(memory_db):
    created, existing = memory_db
    existing.append({"content": "我叫张三"})
    out = await mem.extract_memories_from_message("你好，我叫张三")
    assert out == []
    assert created == []


@pytest.mark.anyio
async def test_extract_records_source_conversation(memory_db):
    created, _ = memory_db
    await mem.extract_memories_from_message("我叫李四", conversation_id="conv-42")
    assert created[0]["source_conversation_id"] == "conv-42"


@pytest.mark.anyio
async def test_extract_survives_db_failure(monkeypatch, memory_db):
    """记忆抽取是旁路功能，写库失败绝不能影响主对话。"""
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(mem.db, "add_memory", boom)
    assert await mem.extract_memories_from_message("我叫王五") == []


@pytest.mark.anyio
async def test_extract_can_capture_multiple_facts_in_one_message(memory_db):
    created, _ = memory_db
    out = await mem.extract_memories_from_message("我叫赵六，我喜欢喝咖啡")
    categories = {m["category"] for m in out}
    assert {"fact", "preference"} <= categories


# ---------------------------------------------------------------------------
# get_context_memories
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_uses_semantic_search_when_query_given(monkeypatch):
    called = {}

    async def fake_semantic(query, limit=5, min_importance=0.3):
        called["query"], called["limit"] = query, limit
        return [{"memory_id": 7, "content": "用户叫张三", "category": "fact", "importance": 0.8}]

    monkeypatch.setattr(mem.vector_memory, "search_memories_semantic", fake_semantic)
    out = await mem.get_context_memories(limit=3, query="用户叫什么")
    assert called == {"query": "用户叫什么", "limit": 3}
    assert "用户叫张三" in out
    assert "0.8" in out


@pytest.mark.anyio
async def test_context_falls_back_to_heuristic_without_query(monkeypatch):
    async def fake_for_context(limit=5, min_importance=0.3):
        return [{"id": 1, "content": "用户喜欢 Python", "category": "preference", "importance": 0.7}]

    monkeypatch.setattr(mem.db, "get_memories_for_context", fake_for_context)
    out = await mem.get_context_memories(limit=5)
    assert "用户喜欢 Python" in out


@pytest.mark.anyio
async def test_context_returns_empty_when_no_memories(monkeypatch):
    async def fake_for_context(limit=5, min_importance=0.3):
        return []

    monkeypatch.setattr(mem.db, "get_memories_for_context", fake_for_context)
    assert await mem.get_context_memories() == ""


@pytest.mark.anyio
async def test_context_degrades_to_empty_on_error(monkeypatch):
    """召回失败（ChromaDB 挂了等）必须静默返回空串，不能污染 system prompt。"""
    async def boom(*a, **kw):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(mem.db, "get_memories_for_context", boom)
    assert await mem.get_context_memories() == ""


# ---------------------------------------------------------------------------
# search_and_respond
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_and_respond_formats_results(monkeypatch):
    async def fake_search(query, limit=5):
        return [
            {"category": "fact", "content": "用户叫张三"},
            {"category": "preference", "content": "用户喜欢 Python"},
        ]

    monkeypatch.setattr(mem.db, "search_memories", fake_search)
    out = await mem.search_and_respond("用户是谁")
    assert out.startswith("找到以下相关记忆:")
    assert "[fact] 用户叫张三" in out
    assert "[preference] 用户喜欢 Python" in out


@pytest.mark.anyio
async def test_search_and_respond_empty_message(monkeypatch):
    async def fake_search(query, limit=5):
        return []

    monkeypatch.setattr(mem.db, "search_memories", fake_search)
    assert await mem.search_and_respond("随便") == "没有找到相关记忆。"


@pytest.mark.anyio
async def test_search_and_respond_reports_failure(monkeypatch):
    async def boom(query, limit=5):
        raise RuntimeError("fts broken")

    monkeypatch.setattr(mem.db, "search_memories", boom)
    out = await mem.search_and_respond("x")
    assert out.startswith("记忆搜索失败:")
    assert "fts broken" in out


# ---------------------------------------------------------------------------
# run_memory_housekeeping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_housekeeping_reports_counts(monkeypatch):
    async def fake_forget(min_confidence=0.2, min_age_days=30):
        return 3

    async def fake_archive(min_age_days=180):
        return 5

    monkeypatch.setattr(mem.db, "forget_low_confidence_memories", fake_forget)
    monkeypatch.setattr(mem.db, "archive_stale_memories", fake_archive)
    assert await mem.run_memory_housekeeping() == {"forgotten": 3, "archived": 5}
