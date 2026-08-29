"""JARVIS model router — fast/mid/deep tier selection and profile resolution.

The router automatically selects an appropriate model tier for each LLM call
based on message complexity, attachments, tool usage and conversation depth.
Callers can also force a specific tier via ``explicit_tier``.

Tiers:
- fast:  cheap / quick models for greetings, short Q&A, simple instructions
- mid:   balanced models for general chat, summarisation, image/file tasks
- deep:  capable / reasoning models for coding, analysis, long context, multi-step
"""

from __future__ import annotations

import logging

from .config import settings
from .database import get_model_profiles
from .models import DEFAULT_TIER_MAP, Tier

logger = logging.getLogger("jarvis")

# Rough thresholds used by the heuristic selector.
_FAST_MAX_CHARS = 250
_DEEP_MIN_CHARS = 2000
_DEEP_MIN_HISTORY = 12


def _content_length(messages: list[dict]) -> int:
    """Estimate the total text length of the request messages."""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(str(part.get("text", "")))
    return total


def _has_attachments(messages: list[dict]) -> bool:
    """Return True if any message contains an image or file attachment."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "file"):
                    return True
        # Also detect legacy JSON payloads stored in the database
        if isinstance(content, str) and content.strip().startswith("{"):
            try:
                import json
                payload = json.loads(content)
                if payload.get("images") or payload.get("files"):
                    return True
            except Exception:
                pass
    return False


def _contains_code_or_reasoning_keywords(text: str) -> bool:
    """Detect common signals that indicate a complex / reasoning task."""
    keywords = [
        "代码", "编程", "code", "programming", "debug", "bug",
        "分析", "analyze", "analysis", "reasoning", "推理",
        "解释", "explain", "compare", "对比", "总结", "summarize",
        "设计", "design", "架构", "architecture", "implement",
        "实现", "优化", "optimize", "算法", "algorithm",
    ]
    lowered = text.lower()
    return any(k in lowered for k in keywords)


def select_tier(
    messages: list[dict],
    tools: list[dict] | None = None,
    explicit_tier: Tier | None = None,
) -> Tier:
    """Choose the most appropriate model tier for a request.

    Args:
        messages:       OpenAI-format message list.
        tools:          Optional tool schemas; presence bumps tier to at least mid.
        explicit_tier:  If provided, overrides automatic selection.

    Returns:
        One of "fast", "mid", "deep".
    """
    if explicit_tier in ("fast", "mid", "deep"):
        logger.debug("Model tier forced by caller: %s", explicit_tier)
        return explicit_tier

    content_len = _content_length(messages)
    history_len = max(0, len([m for m in messages if m.get("role") != "system"]))
    has_attachments = _has_attachments(messages)
    has_tools = bool(tools)

    # Combine all text for keyword scanning (first 4000 chars is enough)
    all_text = " ".join(
        str(m.get("content", "")) for m in messages if isinstance(m.get("content"), (str, list))
    )[:4000]
    complex_signals = _contains_code_or_reasoning_keywords(all_text)

    # Deep triggers
    if content_len >= _DEEP_MIN_CHARS or history_len >= _DEEP_MIN_HISTORY:
        logger.debug("Auto-selected tier: deep (content=%d history=%d)", content_len, history_len)
        return "deep"

    # Mid triggers
    if has_attachments or has_tools or complex_signals or content_len >= _FAST_MAX_CHARS:
        logger.debug(
            "Auto-selected tier: mid (attachments=%s tools=%s complex=%s content=%d)",
            has_attachments,
            has_tools,
            complex_signals,
            content_len,
        )
        return "mid"

    logger.debug("Auto-selected tier: fast (content=%d)", content_len)
    return "fast"


async def resolve_profile(tier: Tier) -> dict:
    """Return the active model profile that matches ``tier``.

    Falls back through tiers if no profile is available for the requested tier:
    fast -> mid -> deep -> whatever is active.
    """
    chain = await resolve_profile_chain(tier)
    if not chain:
        raise RuntimeError("No model profile is configured")
    return chain[0]


async def resolve_profile_chain(tier: Tier) -> list[dict]:
    """Return candidate profiles in fallback order for ``tier``.

    The first profile is the preferred one; later profiles are used as
    runtime fallbacks when the preferred model is unavailable.
    """
    profiles = await get_model_profiles()

    def first(t: Tier) -> dict | None:
        return next((p for p in profiles if p.get("tier") == t), None)

    chain: list[dict] = []
    seen: set[str] = set()

    for candidate_tier in (tier, "mid", "deep", "fast"):
        profile = first(candidate_tier)
        if profile and profile["id"] not in seen:
            seen.add(profile["id"])
            chain.append(profile)

    # Last resort: use whatever is currently active
    active = next((p for p in profiles if p.get("is_active")), None)
    if active and active["id"] not in seen:
        chain.append(active)

    return chain


def apply_profile_to_settings(profile: dict) -> None:
    """Update runtime settings to use the chosen profile."""
    settings.llm_model = profile["model"]
    settings.llm_base_url = profile["base_url"]
    if profile.get("api_key"):
        settings.llm_api_key = profile["api_key"]


def profile_to_api_credentials(profile: dict) -> tuple[str, str, str]:
    """Return (base_url, api_key, model_id) for a profile."""
    api_key = profile.get("api_key") or settings.llm_api_key
    return profile["base_url"], api_key, profile["model"]


def get_profile_tier(profile_id: str) -> Tier:
    """Return the default tier for a built-in profile id.

    Custom profiles return "mid" by default unless overridden by the caller.
    """
    return DEFAULT_TIER_MAP.get(profile_id, "mid")
