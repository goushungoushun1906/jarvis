"""Calendar plugin — event management with mock data storage."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")


# ── Mock event database ────────────────────────────────────────────────
# In a real implementation, this would persist to a database or external API.

def _generate_mock_events() -> list[dict]:
    """Generate sample events for demonstration."""
    today = datetime.now()
    return [
        {
            "id": str(uuid.uuid4())[:8],
            "title": "团队周会",
            "date": today.strftime("%Y-%m-%d"),
            "start_time": "09:00",
            "end_time": "10:00",
            "attendees": "张三, 李四, 王五",
            "location": "会议室 A",
            "notes": "讨论本周 Sprint 进度",
            "created_at": today.isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "产品评审",
            "date": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
            "start_time": "14:00",
            "end_time": "15:30",
            "attendees": "赵六, 钱七",
            "location": "线上会议",
            "notes": "Q3 产品路线图评审",
            "created_at": today.isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "技术分享会",
            "date": (today + timedelta(days=3)).strftime("%Y-%m-%d"),
            "start_time": "16:00",
            "end_time": "17:00",
            "attendees": "全体工程师",
            "location": "301 会议室",
            "notes": "主题：微服务架构演进",
            "created_at": today.isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "客户拜访",
            "date": (today + timedelta(days=5)).strftime("%Y-%m-%d"),
            "start_time": "10:00",
            "end_time": "12:00",
            "attendees": "孙八",
            "location": "客户公司",
            "notes": "需求沟通和技术方案讨论",
            "created_at": today.isoformat(),
        },
    ]


# In-memory event store (mock)
_mock_events: list[dict] = _generate_mock_events()


class CalendarPlugin(BasePlugin):
    """A plugin that provides calendar event management."""

    async def on_load(self) -> None:
        logger.info("CalendarPlugin v%s loaded with %d mock events", self.version, len(_mock_events))

    async def on_enable(self) -> None:
        logger.info("CalendarPlugin enabled")

    async def on_disable(self) -> None:
        logger.info("CalendarPlugin disabled")

    async def on_unload(self) -> None:
        logger.info("CalendarPlugin unloaded")

    # ── Command handlers ───────────────────────────────────────────────

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        if command_name == "list_events":
            return await self._handle_list_events(args)
        if command_name == "create_event":
            return await self._handle_create_event(args)
        if command_name == "search_events":
            return await self._handle_search_events(args)
        return ToolResult(f"Unknown command: {command_name}", success=False)

    async def _handle_list_events(self, args: dict[str, Any]) -> ToolResult:
        """List events within a date range."""
        limit = int(args.get("limit", 20))
        start_str = args.get("start_date")
        end_str = args.get("end_date")

        # Parse dates
        today = datetime.now().date()
        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else today
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else start_date
        except ValueError:
            return ToolResult("日期格式错误，请使用 YYYY-MM-DD 格式", success=False)

        # Filter events
        matching = []
        for evt in _mock_events:
            try:
                evt_date = datetime.strptime(evt["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if start_date <= evt_date <= end_date:
                matching.append(evt)

        matching.sort(key=lambda e: (e["date"], e.get("start_time", "")))
        matching = matching[:limit]

        if not matching:
            return ToolResult(
                f"在 {start_str or '今天'} 至 {end_str or start_str or '今天'} 期间没有找到事件。",
                success=True,
                metadata={"count": 0},
            )

        lines = [f"找到 {len(matching)} 个事件：\n"]
        for i, evt in enumerate(matching, 1):
            lines.append(
                f"{i}. [{evt['date']} {evt.get('start_time', '?')} - {evt.get('end_time', '?')}]"
                f" {evt['title']}"
                f"{' @' + evt['location'] if evt.get('location') else ''}"
            )
            if evt.get("attendees"):
                lines.append(f"   参会人: {evt['attendees']}")
            if evt.get("notes"):
                lines.append(f"   备注: {evt['notes']}")
            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"count": len(matching), "events": matching},
        )

    async def _handle_create_event(self, args: dict[str, Any]) -> ToolResult:
        """Create a new calendar event."""
        title = args.get("title", "").strip()
        date_str = args.get("date", "").strip()

        if not title:
            return ToolResult("事件标题不能为空", success=False)
        if not date_str:
            return ToolResult("事件日期不能为空", success=False)

        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return ToolResult("日期格式错误，请使用 YYYY-MM-DD 格式", success=False)

        new_event = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "date": date_str,
            "start_time": args.get("start_time", "09:00"),
            "end_time": args.get("end_time", "10:00"),
            "attendees": args.get("attendees", ""),
            "location": args.get("location", ""),
            "notes": args.get("notes", ""),
            "created_at": datetime.now().isoformat(),
        }

        _mock_events.append(new_event)

        lines = [
            f"✅ 事件已创建：",
            f"  标题: {title}",
            f"  日期: {date_str}",
            f"  时间: {new_event['start_time']} - {new_event['end_time']}",
        ]
        if new_event["location"]:
            lines.append(f"  地点: {new_event['location']}")
        if new_event["attendees"]:
            lines.append(f"  参会人: {new_event['attendees']}")
        if new_event["notes"]:
            lines.append(f"  备注: {new_event['notes']}")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"event": new_event},
        )

    async def _handle_search_events(self, args: dict[str, Any]) -> ToolResult:
        """Search events by keyword."""
        keyword = args.get("keyword", "").strip().lower()
        date_range = args.get("date_range", "").strip()

        if not keyword:
            return ToolResult("搜索关键词不能为空", success=False)

        # Parse date range filter
        today = datetime.now().date()
        filter_start = None
        filter_end = None

        if date_range:
            if date_range == "today":
                filter_start = filter_end = today
            elif date_range == "this week":
                filter_start = today
                filter_end = today + timedelta(days=7 - today.weekday())
            elif date_range == "next week":
                next_monday = today + timedelta(days=(7 - today.weekday()) % 7 + 7)
                filter_start = next_monday
                filter_end = next_monday + timedelta(days=6)
            else:
                try:
                    filter_start = filter_end = datetime.strptime(date_range, "%Y-%m-%d").date()
                except ValueError:
                    pass

        matching = []
        for evt in _mock_events:
            # Match keyword against title, location, notes
            searchable = (
                evt.get("title", "") + " " + evt.get("location", "") + " " + evt.get("notes", "")
            ).lower()
            if keyword not in searchable:
                continue

            # Apply date range filter
            if filter_start and filter_end:
                try:
                    evt_date = datetime.strptime(evt["date"], "%Y-%m-%d").date()
                    if not (filter_start <= evt_date <= filter_end):
                        continue
                except ValueError:
                    pass

            matching.append(evt)

        if not matching:
            return ToolResult(
                f"没有找到包含关键词「{args.get('keyword', '')}」的事件。",
                success=True,
                metadata={"count": 0},
            )

        lines = [f"找到 {len(matching)} 个匹配「{args.get('keyword', '')}」的事件：\n"]
        for i, evt in enumerate(matching, 1):
            lines.append(
                f"{i}. [{evt['date']} {evt.get('start_time', '?')}]"
                f" {evt['title']}"
                f"{' @' + evt['location'] if evt.get('location') else ''}"
            )
            if evt.get("notes"):
                lines.append(f"   {evt['notes']}")
            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={"count": len(matching), "events": matching},
        )
