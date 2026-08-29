"""System Monitor plugin — system resource monitoring with mock data."""

from __future__ import annotations

import logging
import random
from typing import Any

from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")


# ── Mock data helpers ──────────────────────────────────────────────────
# These generate realistic psutil-like values without requiring psutil.

_MOCK_PROCESS_POOL: list[dict] = [
    {"name": "chrome.exe", "cpu_base": 8.3, "mem_base": 450.0},
    {"name": "python.exe", "cpu_base": 12.1, "mem_base": 320.0},
    {"name": "node.exe", "cpu_base": 6.7, "mem_base": 280.0},
    {"name": "explorer.exe", "cpu_base": 1.2, "mem_base": 85.0},
    {"name": "vscode.exe", "cpu_base": 5.5, "mem_base": 520.0},
    {"name": "discord.exe", "cpu_base": 3.2, "mem_base": 380.0},
    {"name": "slack.exe", "cpu_base": 4.1, "mem_base": 350.0},
    {"name": "spotify.exe", "cpu_base": 2.8, "mem_base": 290.0},
    {"name": "msedge.exe", "cpu_base": 7.4, "mem_base": 410.0},
    {"name": "java.exe", "cpu_base": 15.6, "mem_base": 680.0},
    {"name": "mysql.exe", "cpu_base": 4.5, "mem_base": 550.0},
    {"name": "nginx.exe", "cpu_base": 0.8, "mem_base": 45.0},
    {"name": "postgres.exe", "cpu_base": 3.9, "mem_base": 490.0},
    {"name": "docker.exe", "cpu_base": 5.0, "mem_base": 610.0},
    {"name": "teams.exe", "cpu_base": 6.3, "mem_base": 370.0},
]

_CPU_CORES: int = 8
_RAM_TOTAL_GB: float = 16.0
_DISK_TOTAL_GB: float = 476.0


def _mock_cpu_percent() -> float:
    """Return a mock CPU usage percentage between 25% and 45%."""
    return round(random.uniform(25.0, 45.0), 1)


def _mock_load_averages(cores: int = _CPU_CORES) -> tuple[float, float, float]:
    """Return mock 1/5/15 minute load averages scaled by core count."""
    base = random.uniform(0.6, 1.2) * cores
    return (
        round(base, 2),
        round(base * random.uniform(0.85, 1.05), 2),
        round(base * random.uniform(0.75, 1.0), 2),
    )


def _mock_memory() -> dict:
    """Return mock memory stats for 16 GB RAM."""
    total = _RAM_TOTAL_GB
    used = round(random.uniform(8.0, 12.0), 1)
    free = round(total - used, 1)
    percent = round((used / total) * 100, 1)
    return {"total": total, "used": used, "free": free, "percent": percent}


def _mock_disk(path: str) -> dict:
    """Return mock disk usage for the given path."""
    total = _DISK_TOTAL_GB
    used = round(random.uniform(120.0, 300.0), 1)
    free = round(total - used, 1)
    percent = round((used / total) * 100, 1)
    return {
        "path": path,
        "total": total,
        "used": used,
        "free": free,
        "percent": percent,
    }


def _mock_processes(sort_by: str, limit: int) -> list[dict]:
    """Return a list of mock process entries."""
    processes: list[dict] = []
    pid_base = random.randint(1000, 9000)
    for i, proc in enumerate(_MOCK_PROCESS_POOL):
        cpu_jitter = random.uniform(-3.0, 3.0)
        mem_jitter = random.uniform(-80.0, 80.0)
        processes.append(
            {
                "pid": pid_base + i,
                "name": proc["name"],
                "cpu_percent": round(max(0.1, proc["cpu_base"] + cpu_jitter), 1),
                "memory_mb": round(max(10.0, proc["mem_base"] + mem_jitter), 1),
            }
        )

    # Sort and slice
    reverse = True
    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    processes.sort(key=lambda p: p[key], reverse=reverse)
    return processes[:limit]


class SystemMonitorPlugin(BasePlugin):
    """A plugin that provides system resource monitoring.

    Reports CPU usage, memory consumption, disk usage, and top running
    processes.  Uses mock data that mimics real psutil outputs.
    """

    # ── Lifecycle hooks ────────────────────────────────────────────────

    async def on_load(self) -> None:
        logger.info("SystemMonitorPlugin v%s loaded", self.version)

    async def on_enable(self) -> None:
        logger.info("SystemMonitorPlugin enabled — monitoring started")

    async def on_disable(self) -> None:
        logger.info("SystemMonitorPlugin disabled — monitoring paused")

    async def on_unload(self) -> None:
        logger.info("SystemMonitorPlugin unloaded")

    # ── Command dispatch ───────────────────────────────────────────────

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch command by name."""
        if command_name == "get_cpu_usage":
            return await self._handle_get_cpu_usage(args)
        if command_name == "get_memory_usage":
            return await self._handle_get_memory_usage(args)
        if command_name == "get_disk_usage":
            return await self._handle_get_disk_usage(args)
        if command_name == "list_processes":
            return await self._handle_list_processes(args)
        return ToolResult(
            f"Unknown command: {command_name}", success=False
        )

    # ── Command handlers ───────────────────────────────────────────────

    async def _handle_get_cpu_usage(self, args: dict[str, Any]) -> ToolResult:
        """Get current CPU usage percentage and load averages."""
        cpu_percent = _mock_cpu_percent()
        load_1, load_5, load_15 = _mock_load_averages()

        content_lines = [
            f"CPU Usage: {cpu_percent}%",
            f"  Cores: {_CPU_CORES}",
            f"  Load Average (1 min):  {load_1}",
            f"  Load Average (5 min):  {load_5}",
            f"  Load Average (15 min): {load_15}",
        ]

        return ToolResult(
            content="\n".join(content_lines),
            success=True,
            metadata={
                "cpu_percent": cpu_percent,
                "core_count": _CPU_CORES,
                "load_average": {
                    "1min": load_1,
                    "5min": load_5,
                    "15min": load_15,
                },
            },
        )

    async def _handle_get_memory_usage(self, args: dict[str, Any]) -> ToolResult:
        """Get current memory usage (used, free, total)."""
        mem = _mock_memory()

        content_lines = [
            f"Memory Usage:",
            f"  Total: {mem['total']} GB",
            f"  Used:  {mem['used']} GB ({mem['percent']}%)",
            f"  Free:  {mem['free']} GB",
        ]

        return ToolResult(
            content="\n".join(content_lines),
            success=True,
            metadata={
                "total_gb": mem["total"],
                "used_gb": mem["used"],
                "free_gb": mem["free"],
                "percent": mem["percent"],
            },
        )

    async def _handle_get_disk_usage(self, args: dict[str, Any]) -> ToolResult:
        """Get disk usage for a specified path."""
        path = args.get("path", "C:\\")
        disk = _mock_disk(path)

        content_lines = [
            f"Disk Usage for {path}:",
            f"  Total: {disk['total']} GB",
            f"  Used:  {disk['used']} GB ({disk['percent']}%)",
            f"  Free:  {disk['free']} GB",
        ]

        return ToolResult(
            content="\n".join(content_lines),
            success=True,
            metadata={
                "path": path,
                "total_gb": disk["total"],
                "used_gb": disk["used"],
                "free_gb": disk["free"],
                "percent": disk["percent"],
            },
        )

    async def _handle_list_processes(self, args: dict[str, Any]) -> ToolResult:
        """List top running processes by CPU or memory."""
        sort_by = args.get("sort_by", "cpu")
        limit = int(args.get("limit", 10))

        # Clamp limit
        if limit < 1:
            limit = 1
        elif limit > 50:
            limit = 50

        processes = _mock_processes(sort_by, limit)
        sort_label = "CPU" if sort_by == "cpu" else "Memory"

        content_lines = [
            f"Top {len(processes)} Processes (by {sort_label}):",
            f"{'PID':>7}  {'Name':<20} {'CPU%':>7} {'MEM(MB)':>9}",
            "-" * 52,
        ]
        for proc in processes:
            content_lines.append(
                f"{proc['pid']:>7}  {proc['name']:<20} {proc['cpu_percent']:>6.1f}% {proc['memory_mb']:>8.1f}"
            )

        return ToolResult(
            content="\n".join(content_lines),
            success=True,
            metadata={
                "sort_by": sort_by,
                "limit": limit,
                "count": len(processes),
                "processes": processes,
            },
        )
