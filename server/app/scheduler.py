"""JARVIS Scheduler — scheduled tasks using APScheduler.

Built-in jobs:
- morning_digest: Daily morning briefing (weather + calendar + reminders)
- Status tracking via API
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("jarvis.scheduler")

# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None


async def _memory_housekeeping() -> None:
    """Daily memory cleanup: forget low-confidence memories and archive stale ones."""
    logger.info("Running memory housekeeping...")
    try:
        from app import memory as mem
        result = await mem.run_memory_housekeeping()
        logger.info("Memory housekeeping done: %s", result)
    except Exception as e:
        logger.error("Memory housekeeping failed: %s", e)


async def _morning_digest() -> None:
    """Generate a daily morning briefing by calling built-in tools."""
    logger.info("Generating morning digest...")

    from app.builtins import WebSearchTool, SystemInfoTool

    parts: list[str] = []
    now = datetime.now()

    # Time greeting
    hour = now.hour
    if hour < 12:
        greeting = "早上好"
    elif hour < 18:
        greeting = "下午好"
    else:
        greeting = "晚上好"
    parts.append(f"=== {greeting}，这是你的每日简报 ({now.strftime('%Y-%m-%d %H:%M')}) ===\n")

    # System time
    sys_tool = SystemInfoTool()
    time_result = await sys_tool.execute("time")
    if time_result.success:
        parts.append(f"** 当前时间 **\n{time_result.content}\n")

    # Try to get weather (from weather plugin)
    try:
        from app.plugin_manager import plugin_manager
        weather_result = await plugin_manager.execute_command(
            "weather", "get_weather", {"city": ""}
        )
        if weather_result.success:
            parts.append(f"** 天气 **\n{weather_result.content}\n")
    except Exception as e:
        logger.debug("Weather digest failed: %s", e)
        parts.append("** 天气 **\n(天气插件未响应)\n")

    # Try to get today's events (from calendar plugin)
    try:
        from app.plugin_manager import plugin_manager
        cal_result = await plugin_manager.execute_command(
            "calendar", "list_events", {"date": now.strftime("%Y-%m-%d")}
        )
        if cal_result.success:
            parts.append(f"** 今日日程 **\n{cal_result.content}\n")
    except Exception as e:
        logger.debug("Calendar digest failed: %s", e)
        parts.append("** 今日日程 **\n(日历插件未响应)\n")

    # Try to get reminders
    try:
        from app.plugin_manager import plugin_manager
        rem_result = await plugin_manager.execute_command(
            "reminder", "list_reminders", {}
        )
        if rem_result.success:
            parts.append(f"** 待办提醒 **\n{rem_result.content}")
    except Exception as e:
        logger.debug("Reminder digest failed: %s", e)

    digest = "\n".join(parts)
    logger.info("Morning digest generated:\n%s", digest)

    # Store digest in memory for retrieval
    try:
        from app import memory as mem
        await mem.db.add_memory(
            content=f"每日简报 ({now.strftime('%m-%d')}): {greeting}",
            category="daily_digest",
            importance=0.9,
        )
    except Exception:
        pass

    # Store for API retrieval
    _last_digest = {
        "content": digest,
        "timestamp": now.isoformat(),
        "type": "morning_digest",
    }
    import app.scheduler as self_mod
    self_mod._stored_digest = _last_digest


# Stored digest for API retrieval
_stored_digest: dict | None = None


def get_stored_digest() -> dict | None:
    global _stored_digest
    return _stored_digest


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def get_jobs_info() -> list[dict]:
    """Get info about all scheduled jobs."""
    if not _scheduler:
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs


async def start_scheduler() -> dict:
    """Start the APScheduler with built-in jobs."""
    global _scheduler

    if _scheduler and _scheduler.running:
        return {"status": "already_running", "jobs": get_jobs_info()}

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # Morning digest: every day at 7:00 AM
    _scheduler.add_job(
        _morning_digest,
        CronTrigger(hour=7, minute=0, timezone="Asia/Shanghai"),
        id="morning_digest",
        name="每日简报",
        replace_existing=True,
    )

    # Memory housekeeping: every day at 3:00 AM (low-activity period)
    _scheduler.add_job(
        _memory_housekeeping,
        CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
        id="memory_housekeeping",
        name="记忆清理",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started: morning_digest at 07:00 daily, memory_housekeeping at 03:00 daily")

    return {"status": "started", "jobs": get_jobs_info()}


def stop_scheduler() -> dict:
    global _scheduler
    if not _scheduler:
        return {"status": "not_running"}
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")
    return {"status": "stopped"}
