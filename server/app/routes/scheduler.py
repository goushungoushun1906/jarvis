"""Scheduler API routes — manage scheduled tasks."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.scheduler import (
    start_scheduler,
    stop_scheduler,
    get_scheduler,
    get_jobs_info,
    get_stored_digest,
    _morning_digest,
)

logger = logging.getLogger("jarvis.scheduler.routes")
router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/info")
async def get_scheduler_info():
    """Get scheduler status and job list."""
    sched = get_scheduler()
    if not sched or not sched.running:
        return {"status": "stopped", "jobs": []}
    return {"status": "running", "jobs": get_jobs_info()}


@router.post("/start")
async def start_sched():
    """Start the scheduler."""
    return await start_scheduler()


@router.post("/stop")
async def stop_sched():
    """Stop the scheduler."""
    return stop_scheduler()


@router.post("/trigger-digest")
async def trigger_digest():
    """Manually trigger the morning digest."""
    await _morning_digest()
    digest = get_stored_digest()
    return {"triggered": True, "digest": digest}


@router.get("/digest")
async def get_digest():
    """Get the last generated digest."""
    digest = get_stored_digest()
    if digest:
        return digest
    return {"status": "no_digest", "message": "No digest generated yet. Trigger one or wait for 07:00."}
