"""Memory management for JARVIS — auto-extract and query memories."""

from __future__ import annotations

import logging
import re

from . import database as db
from . import vector_memory

logger = logging.getLogger("jarvis")

# Patterns that suggest important information to remember
# Tuple: (regex, category, importance, base_confidence)
MEMORY_PATTERNS: list[tuple[str, str, float, float]] = [
    (r"我叫.{1,10}", "fact", 0.8, 0.9),                # User's name
    (r"我(?:喜欢|爱|偏好).{1,30}", "preference", 0.7, 0.8),  # User preferences
    (r"(?:记住|记住这个).{1,50}", "general", 0.9, 0.95),      # Explicit remember request
    (r"我(?:是|在).{1,30}(?:工作|上班)", "fact", 0.6, 0.7),  # User's job
    (r"(?:生日|纪念日).{1,20}", "event", 0.8, 0.85),         # Important dates
    (r"我(?:住|住在).{1,30}", "fact", 0.5, 0.6),            # User's location
    (r"(?:别|不要|忌讳).{1,30}", "preference", 0.7, 0.75),    # Aversions
]


def _score_confidence(content: str, category: str, base_confidence: float) -> float:
    """Score memory confidence based on explicitness, length and category.

    - Explicit remember requests get a big boost.
    - Very short or very long snippets lose confidence.
    - Concrete facts and preferences score higher than general statements.
    """
    score = base_confidence
    content_len = len(content)

    # Explicit request booster
    if category == "general" and "记住" in content:
        score += 0.05

    # Length penalties
    if content_len < 5:
        score -= 0.2
    elif content_len > 200:
        score -= 0.1

    # Category modifiers
    if category == "fact":
        score += 0.03
    elif category == "preference":
        score += 0.02
    elif category == "event":
        score += 0.04

    return max(0.0, min(1.0, score))


async def extract_memories_from_message(
    text: str,
    conversation_id: str = None,
) -> list[dict]:
    """Scan a message for information worth remembering.
    Returns list of newly created memories."""
    created: list[dict] = []
    for pattern, category, importance, base_confidence in MEMORY_PATTERNS:
        match = re.search(pattern, text)
        if match:
            content = match.group(0).strip()
            # Skip if we already have a very similar memory
            existing = await db.search_memories(content[:20], limit=3)
            if any(e["content"] == content for e in existing):
                continue
            confidence = _score_confidence(content, category, base_confidence)
            try:
                memory = await db.add_memory(
                    content=content,
                    category=category,
                    importance=importance,
                    confidence=confidence,
                    source_conversation_id=conversation_id,
                )
                created.append(memory)
                logger.info(
                    "Auto-extracted memory: %s (cat=%s, imp=%.1f, conf=%.2f)",
                    content, category, importance, confidence,
                )
            except Exception as e:
                logger.error("Failed to save extracted memory: %s", e)
    return created


async def run_memory_housekeeping() -> dict:
    """Forget low-confidence memories and archive stale ones.

    Should be called periodically (e.g. once per day via scheduler).
    """
    forgotten = await db.forget_low_confidence_memories()
    archived = await db.archive_stale_memories()
    return {"forgotten": forgotten, "archived": archived}


async def get_context_memories(
    limit: int = 5,
    query: str | None = None,
    min_importance: float = 0.3,
) -> str:
    """Format relevant memories for injection into system prompt.

    If *query* is provided, uses ChromaDB semantic search to recall the most
    relevant memories. Otherwise falls back to the importance/recency heuristic.

    Returns a string like:
    '## 关于用户的记忆\n- 用户叫张三 (重要性: 0.8)\n- 用户喜欢Python (重要性: 0.7)'
    """
    try:
        if query:
            results = await vector_memory.search_memories_semantic(
                query=query,
                limit=limit,
                min_importance=min_importance,
            )
            memories = [
                {
                    "id": r["memory_id"],
                    "content": r["content"],
                    "category": r["category"],
                    "importance": r["importance"],
                }
                for r in results
            ]
        else:
            memories = await db.get_memories_for_context(
                limit=limit, min_importance=min_importance
            )
    except Exception as e:
        logger.error("Failed to load context memories: %s", e)
        return ""

    if not memories:
        return ""

    lines: list[str] = []
    for m in memories:
        lines.append(f"- {m['content']} (重要性: {m['importance']})")
    return "\n".join(lines)


async def search_and_respond(query: str, limit: int = 5) -> str:
    """Search memories and format results for inclusion in LLM response."""
    try:
        results = await db.search_memories(query, limit=limit)
    except Exception as e:
        logger.error("Memory search failed: %s", e)
        return f"记忆搜索失败: {e}"

    if not results:
        return "没有找到相关记忆。"

    lines: list[str] = ["找到以下相关记忆:"]
    for r in results:
        lines.append(f"- [{r['category']}] {r['content']}")
    return "\n".join(lines)
