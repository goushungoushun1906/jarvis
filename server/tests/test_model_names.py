"""Tests for model-name normalisation and validation (Phase 17.10).

Guards the fix for profiles saved with a marketing-style model name
(e.g. ``DeepSeek-V4.1-Flash``) that the gateway does not accept.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.model_names import (  # noqa: E402
    MODEL_ALIASES,
    _slug,
    looks_like_marketing_name,
    normalize_model_id,
    suggest_model_id,
)


# ---------------------------------------------------------------------------
# normalize_model_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The bug this module exists for.
        ("DeepSeek-V4.1-Flash", "deepseek-flash"),
        ("deepseek-v4.1-flash", "deepseek-flash"),
        ("DeepSeek_V4_1_Flash", "deepseek-flash"),
        ("DeepSeek V4.1 Flash", "deepseek-flash"),
        # Pro variants
        ("DeepSeek-V4.1-Pro", "deepseek-v4-pro"),
        ("DeepSeek-V4-Pro", "deepseek-v4-pro"),
        # Legacy ids keep working
        ("deepseek-chat", "deepseek-chat"),
        ("deepseek-reasoner", "deepseek-reasoner"),
        ("DeepSeek-R1", "deepseek-reasoner"),
    ],
)
def test_normalize_known_aliases(raw: str, expected: str):
    assert normalize_model_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "agnes-2.0-flash",
        "agnes-2.5-flash",
        "gpt-4o",
        "claude-sonnet-4-20250514",
        "my-custom-model-v1",
    ],
)
def test_normalize_leaves_unknown_names_untouched(raw: str):
    """We never invent an id — unknown names pass through unchanged."""
    assert normalize_model_id(raw) == raw


def test_normalize_empty_string():
    assert normalize_model_id("") == ""


def test_normalize_is_idempotent():
    """Normalising an already-canonical id must not change it."""
    for canonical in set(MODEL_ALIASES.values()):
        assert normalize_model_id(canonical) == canonical


def test_slug_collapses_separators():
    assert _slug("DeepSeek  V4.1_Flash") == "deepseek-v4-1-flash"
    assert _slug("  Mixed__Case--Name ") == "mixed-case-name"


# ---------------------------------------------------------------------------
# looks_like_marketing_name
# ---------------------------------------------------------------------------


def test_marketing_name_detection():
    assert looks_like_marketing_name("DeepSeek-V4.1-Flash")
    assert looks_like_marketing_name("GPT-4o")
    assert not looks_like_marketing_name("deepseek-flash")
    assert not looks_like_marketing_name("agnes-2.5-flash")
    assert not looks_like_marketing_name("")


# ---------------------------------------------------------------------------
# suggest_model_id
# ---------------------------------------------------------------------------

_AVAILABLE = ["deepseek-flash", "deepseek-v4-pro"]


def test_suggest_returns_none_when_exact_match_present():
    assert suggest_model_id("deepseek-flash", _AVAILABLE) is None


def test_suggest_returns_none_without_candidates():
    assert suggest_model_id("whatever", []) is None


def test_suggest_prefers_known_alias_over_fuzzy():
    # "DeepSeek-V4.1-Flash" is not literally in the list, but its known alias is.
    assert suggest_model_id("DeepSeek-V4.1-Flash", _AVAILABLE) == "deepseek-flash"


def test_suggest_falls_back_to_token_overlap():
    assert suggest_model_id("deepseek-v4-pro-max", _AVAILABLE) == "deepseek-v4-pro"


def test_suggest_handles_unrelated_name():
    # Should still return something plausible rather than blowing up.
    result = suggest_model_id("totally-unrelated-model", _AVAILABLE)
    assert result is None or result in _AVAILABLE
