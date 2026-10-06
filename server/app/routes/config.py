"""JARVIS configuration routes (model + persona)."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from .. import database as db
from .. import persona as persona_mod
from ..config import settings
from ..model_names import normalize_model_id, validate_model_id
from ..models import (
    ModelConfig,
    ModelProfile,
    ModelProfileUpdate,
    ModelValidateRequest,
    PersonaConfig,
    SwitchModelRequest,
)

logger = logging.getLogger("jarvis")

router = APIRouter(prefix="/api/config", tags=["config"])

_MODEL_KEY = "model_config"


# ------------------------------------------------------------------
# Read current config
# ------------------------------------------------------------------

@router.get("")
async def api_get_config():
    persona = await persona_mod.get_persona_config()
    profiles = await db.get_model_profiles()
    active = next((p for p in profiles if p.get("is_active")), None)
    return {
        "model_config": {
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "active_profile": active,
        },
        "persona_config": persona.model_dump(),
        "available_models": [{"id": p["id"], "name": p["name"], "provider": p["provider"], "is_active": p["is_active"], "tier": p.get("tier", "mid")} for p in profiles],
    }


# ------------------------------------------------------------------
# Update model settings
# ------------------------------------------------------------------

@router.put("/model")
async def api_update_model(body: ModelConfig):
    """Update the runtime model config and persist to DB.

    A lightweight test request is sent to validate the API key + endpoint
    before applying the change.
    """
    # Validate by making a tiny request
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{body.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": body.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, "Validation request timed out — check base_url")
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300] if e.response else ""
        raise HTTPException(
            e.response.status_code if e.response else 502,
            f"Model validation failed: {e.response.status_code if e.response else 'unknown'} — {detail}",
        )
    except Exception as e:
        raise HTTPException(502, f"Model validation error: {e}")

    # Persist to DB and update runtime
    await db.set_setting(_MODEL_KEY, body.model_dump_json())
    settings.llm_model = body.model
    settings.llm_base_url = body.base_url

    logger.info("Model config updated: model=%s base_url=%s", body.model, body.base_url)
    return {"model": body.model, "base_url": body.base_url}


# ------------------------------------------------------------------
# Update persona settings
# ------------------------------------------------------------------

@router.put("/persona")
async def api_update_persona(body: PersonaConfig):
    await persona_mod.save_persona_config(body)
    logger.info("Persona config updated: %s", body.name)
    return body.model_dump()


# ------------------------------------------------------------------
# Model Profiles (presets + custom)
# ------------------------------------------------------------------

@router.get("/models")
async def api_list_models():
    """List all model profiles (presets + custom)."""
    return await db.get_model_profiles()


@router.post("/models")
async def api_add_model(body: ModelProfile):
    """Add a custom model profile."""
    await db.add_model_profile(body.model_dump())
    return body.model_dump()


@router.put("/models/{profile_id}")
async def api_update_model(profile_id: str, body: ModelProfileUpdate):
    """Update an existing custom model profile. Built-in profiles cannot be modified."""
    # Prevent modification of built-in profiles
    builtin_ids = {p["id"] for p in db.BUILTIN_PROFILES}
    if profile_id in builtin_ids:
        raise HTTPException(400, "Cannot modify built-in profiles")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await db.update_model_profile(profile_id, updates)
    if not updated:
        raise HTTPException(404, f"Model profile '{profile_id}' not found")

    # If the updated profile is currently active, refresh runtime settings
    profiles = await db.get_model_profiles()
    active = next((p for p in profiles if p.get("is_active")), None)
    if active and active["id"] == profile_id:
        from ..config import settings
        settings.llm_model = updated.get("model", settings.llm_model)
        settings.llm_base_url = updated.get("base_url", settings.llm_base_url)
        if updated.get("api_key"):
            settings.llm_api_key = updated["api_key"]

    logger.info("Updated model profile: %s", profile_id)
    return updated


@router.delete("/models/{profile_id}")
async def api_delete_model(profile_id: str):
    """Delete a custom model profile."""
    deleted = await db.delete_model_profile(profile_id)
    if not deleted:
        raise HTTPException(400, "Cannot delete built-in profiles")
    return {"deleted": True}


@router.post("/models/validate")
async def api_validate_model(body: ModelValidateRequest):
    """Check whether *model* is an id the gateway actually accepts.

    Returns the ids the gateway advertises plus the closest suggestion, so the
    settings UI can offer a one-click fix instead of a vague failure later.
    Never raises: an unreachable gateway reports ``valid: null``.
    """
    base_url = body.base_url
    api_key = body.api_key

    if (not base_url or not api_key) and body.profile_id:
        profile = next(
            (p for p in await db.get_model_profiles() if p.get("id") == body.profile_id),
            None,
        )
        if profile:
            base_url = base_url or profile.get("base_url", "")
            api_key = api_key or profile.get("api_key", "")

    api_key = api_key or settings.llm_api_key
    if not base_url:
        raise HTTPException(400, "base_url is required")

    result = await validate_model_id(base_url, api_key, body.model)
    result["model"] = body.model
    return result


@router.post("/models/switch")
async def api_switch_model(body: SwitchModelRequest):
    """Switch to a model profile by ID."""
    profile = await db.switch_model_profile(body.profile_id)
    if not profile:
        raise HTTPException(404, f"Model profile '{body.profile_id}' not found")
    logger.info("Switched to model profile: %s (%s)", profile["name"], profile["model"])
    return profile
