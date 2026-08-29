"""JARVIS LLM cost & usage dashboard routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from .. import database as db

logger = logging.getLogger("jarvis")

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/summary")
async def api_costs_summary(days: int = Query(30, ge=1, le=365)):
    """Aggregate usage statistics for the last N days."""
    summary = await db.get_llm_call_summary(days=days)
    return {
        "days": days,
        **summary,
    }


@router.get("/recent")
async def api_costs_recent(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    model: str | None = None,
    tier: str | None = None,
):
    """Recent LLM call records, optionally filtered by model or tier."""
    calls = await db.get_llm_calls(limit=limit, offset=offset, model=model, tier=tier)
    return {"calls": calls, "limit": limit, "offset": offset}


@router.get("/by-model")
async def api_costs_by_model(days: int = Query(30, ge=1, le=365)):
    """Per-model aggregated usage for the last N days."""
    rows = await db.get_llm_calls_by_model(days=days)
    return {"days": days, "models": rows}


@router.get("/by-day")
async def api_costs_by_day(days: int = Query(30, ge=1, le=365)):
    """Daily call counts and estimated cost for the last N days."""
    rows = await db.get_llm_calls_by_day(days=days)
    return {"days": days, "days_data": rows}
