"""Model-name normalisation and validation for JARVIS model profiles.

Gateways are strict about the exact ``model`` id they accept, while users (and
model vendors' marketing pages) write names loosely. A profile saved with
``DeepSeek-V4.1-Flash`` silently fails against DeepSeek's gateway, whose real
id is ``deepseek-flash``.

This module provides:

* :func:`normalize_model_id` — best-effort mapping from a user-written name to
  the id a gateway actually accepts. Applied on every LLM call, so a mis-named
  existing profile starts working without the user editing anything.
* :func:`validate_model_id` — live check against the provider's ``/models``
  endpoint, returning the available ids and the closest match.
"""

from __future__ import annotations

import difflib
import logging
import re

import httpx

logger = logging.getLogger("jarvis")

# ------------------------------------------------------------------
# Known aliases
# ------------------------------------------------------------------

#: Canonical gateway ids for providers we know about, keyed by normalised
#: user-facing name. Keys are matched after :func:`_slug` (lowercase, and
#: ``-``/``_``/``.``/spaces collapsed), so "DeepSeek-V4.1-Flash",
#: "deepseek_v4_1_flash" and "DeepSeek V4.1 Flash" all hit the same entry.
MODEL_ALIASES: dict[str, str] = {
    # DeepSeek — gateway ids are lowercase, marketing names are not.
    "deepseek-v4-1-flash": "deepseek-flash",
    "deepseek-v41-flash": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-flash": "deepseek-flash",
    "deepseek-v4-1-pro": "deepseek-v4-pro",
    "deepseek-v41-pro": "deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-chat": "deepseek-chat",
    "deepseek-v3": "deepseek-chat",
    "deepseek-reasoner": "deepseek-reasoner",
    "deepseek-r1": "deepseek-reasoner",
}

#: Substrings that mark a provider as "OpenAI-compatible / lowercase-ids".
#: Used only to decide whether an unknown name looks like a marketing name
#: (mixed case + version suffix) worth warning about.
_MARKETING_NAME_RE = re.compile(r"[A-Z].*\d")


def _slug(name: str) -> str:
    """Normalise a model name for alias lookup."""
    slug = name.strip().lower()
    slug = re.sub(r"[\s._]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def normalize_model_id(model: str) -> str:
    """Map a possibly-loose model name onto the id the gateway accepts.

    Unknown names are returned unchanged — we never invent an id, we only
    correct names we positively recognise.
    """
    if not model:
        return model
    canonical = MODEL_ALIASES.get(_slug(model))
    if canonical and canonical != model:
        logger.info("Model name normalised: %r -> %r", model, canonical)
        return canonical
    return model


def looks_like_marketing_name(model: str) -> bool:
    """Heuristic: is this a human-facing name rather than a gateway id?

    Gateway ids are conventionally lowercase. A mixed-case name with a version
    number (``DeepSeek-V4.1-Flash``) is a strong signal that it was copied from
    a product page. A name we recognise as an alias but that differs from its
    canonical id counts too.
    """
    if not model:
        return False
    canonical = MODEL_ALIASES.get(_slug(model))
    if canonical and canonical != model:
        return True
    return bool(_MARKETING_NAME_RE.search(model)) and model != model.lower()


# ------------------------------------------------------------------
# Live validation
# ------------------------------------------------------------------


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


async def fetch_available_models(base_url: str, api_key: str, timeout: float = 15.0) -> list[str]:
    """Return the model ids advertised by an OpenAI-compatible gateway.

    Returns an empty list when the gateway has no ``/models`` endpoint or the
    call fails — validation is best-effort and must never block saving.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_models_url(base_url), headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - best effort by design
        logger.debug("Could not list models at %s: %s", base_url, exc)
        return []

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return sorted({str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")})


def suggest_model_id(model: str, available: list[str]) -> str | None:
    """Return the closest available id to *model*, or ``None``."""
    if not available:
        return None
    if model in available:
        return None

    # Prefer a known alias that the gateway actually offers.
    canonical = MODEL_ALIASES.get(_slug(model))
    if canonical and canonical in available:
        return canonical

    matches = difflib.get_close_matches(model, available, n=1, cutoff=0.5)
    if matches:
        return matches[0]

    # Last resort: loose token overlap (e.g. "DeepSeek-V4.1-Flash" vs "deepseek-flash").
    tokens = {t for t in re.split(r"[^a-z0-9]+", model.lower()) if len(t) > 2}
    if tokens:
        best, best_score = None, 0
        for candidate in available:
            cand_tokens = {t for t in re.split(r"[^a-z0-9]+", candidate.lower()) if len(t) > 2}
            score = len(tokens & cand_tokens)
            if score > best_score:
                best, best_score = candidate, score
        if best:
            return best
    return None


async def validate_model_id(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 15.0,
) -> dict:
    """Check *model* against the gateway and describe what to do about it.

    Always returns a dict (never raises) with:
        ``valid``       bool  — exact id is advertised by the gateway
        ``normalized``  str   — id we would actually send after aliasing
        ``available``   list  — ids the gateway advertises (may be empty)
        ``suggestion``  str?  — closest id to use instead, when invalid
        ``message``     str   — human-readable summary
        ``checked``     bool  — whether the gateway could be queried at all
    """
    normalized = normalize_model_id(model)
    available = await fetch_available_models(base_url, api_key, timeout=timeout)

    if not available:
        return {
            "valid": None,  # unknown — could not reach /models
            "normalized": normalized,
            "available": [],
            "suggestion": None,
            "checked": False,
            "message": "无法查询该网关的模型列表，未能校验模型名。",
        }

    valid = normalized in available
    suggestion = None if valid else suggest_model_id(normalized, available)

    if valid:
        message = (
            f"模型名 {normalized} 可用。"
            if normalized == model
            else f"模型名已自动规范化：{model} → {normalized}，网关可用。"
        )
    elif suggestion:
        message = f"网关不认识模型名 {model}，建议使用 {suggestion}。"
    else:
        message = f"网关不认识模型名 {model}，可用模型：{', '.join(available[:10])}"

    return {
        "valid": valid,
        "normalized": normalized,
        "available": available,
        "suggestion": suggestion,
        "checked": True,
        "message": message,
    }
