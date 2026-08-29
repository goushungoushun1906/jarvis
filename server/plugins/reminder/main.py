"""Reminder plugin — to-do and reminder management with mock storage."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")


# ── Mock reminder store ────────────────────────────────────────────────

def _generate_mock_reminders() -> list[dict]:
    """Generate sample reminders for demonstration."""
    today = datetime.now().date()
    return [
        {
            "id": str(uuid.uuid4())[:8],
            "title": "完成周报",
            "due_date": today.strftime("%Y-%m-%d"),
            "priority": "high",
            "category": "work",
            "status": "pending",
            "created_at": (today - timedelta(days=1)).isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "预约牙医",
            "due_date": (today + timedelta(days=7)).strftime("%Y-%m-%d"),
            "priority": "medium",
            "category": "personal",
            "status": "pending",
            "created_at": (today - timedelta(days=2)).isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "购买 groceries",
            "due_date": (today + timedelta(days=2)).strftime("%Y-%m-%d"),
            "priority": "low",
            "category": "personal",
            "status": "pending",
            "created_at": today.isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "title": "提交项目提案",
            "due_date": (today + timedelta(days=14)).strftime("%Y-%m-%d"),
            "priority": "high",
            "category": "work",
            "status": "pending",
            "created_at": (today - timedelta(days=5)).isoformat(),
        },
    ]


_mock_reminders: list[dict] = _generate_mock_reminders()


class ReminderPlugin(BasePlugin):
    """A plugin that provides reminder and to-do management."""

    async def on_load(self) -> None:
        logger.info("ReminderPlugin v%s loaded with %d mock reminders", self.version, len(_mock_reminders))

    async def on_enable(self) -> None:
        logger.info("ReminderPlugin enabled")

    async def on_disable(self) -> None:
        logger.info("ReminderPlugin disabled")

    async def on_unload(self) -> None:
        logger.info("ReminderPlugin unloaded")

    # ── Command handlers ───────────────────────────────────────────────

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        if command_name == "add_reminder":
            return await self._handle_add(args)
        if command_name == "list_reminders":
            return await self._handle_list(args)
        if command_name == "complete_reminder":
            return await self._handle_complete(args)
        if command_name == "delete_reminder":
            return await self._handle_delete(args)
        return ToolResult(f"Unknown command: {command_name}", success=False)

    async def _handle_add(self, args: dict[str, Any]) -> ToolResult:
        title = args.get("title", "").strip()
        if not title:
            return ToolResult("提醒标题不能为空", success=False)

        new_reminder = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "due_date": args.get("due_date", ""),
            "priority": args.get("priority", "medium"),
            "category": args.get("category", "general"),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }

        _mock_reminders.append(new_reminder)

        priority_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
        emoji = priority_map.get(new_reminder["priority"], "📌")

        lines = [
            f"✅ 提醒已创建：",
            f"  ID: {new_reminder['id']}",
            f"  标题: {title}",
            f"  优先级: {emoji} ({new_reminder['priority']})",
        ]
        if new_reminder["due_date"]:
            lines.append(f"  截止日期: {new_reminder['due_date']}")
        if new_reminder["category"]:
            lines.append(f"  分类: {new_reminder['category']}")

        return ToolResult(content="\n".join(lines), success=True, metadata={"reminder": new_reminder})

    async def _handle_list(self, args: dict[str, Any]) -> ToolResult:
        status_filter = args.get("status", "pending")
        priority_filter = args.get("priority", "all")
        category_filter = args.get("category", "")

        filtered = []
        for r in _mock_reminders:
            if status_filter != "all" and r["status"] != status_filter:
                continue
            if priority_filter != "all" and r["priority"] != priority_filter:
                continue
            if category_filter and r.get("category", "") != category_filter:
                continue
            filtered.append(r)

        # Sort: high priority first, then by due date
        priority_order = {"high": 0, "medium": 1, "low": 2}
        filtered.sort(key=lambda r: (
            priority_order.get(r["priority"], 1),
            r.get("due_date", "9999-99-99") or "9999-99-99",
        ))

        if not filtered:
            return ToolResult("没有找到匹配的提醒。", success=True, metadata={"count": 0})

        priority_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}
        status_map = {"pending": "⏳ 待完成", "completed": "✅ 已完成"}

        lines = [f"共有 {len(filtered)} 个提醒：\n"]
        for i, r in enumerate(filtered, 1):
            status_emoji = status_map.get(r["status"], "❓")
            pri_emoji = priority_map.get(r["priority"], "📌")
            lines.append(
                f"{i}. [{r['id']}] {status_emoji} {r['title']} — {pri_emoji}"
            )
            if r.get("due_date"):
                lines.append(f"   截止: {r['due_date']}")
            if r.get("category"):
                lines.append(f"   分类: {r['category']}")
            lines.append("")

        return ToolResult(content="\n".join(lines), success=True, metadata={"count": len(filtered)})

    async def _handle_complete(self, args: dict[str, Any]) -> ToolResult:
        rid = args.get("id", "").strip()
        if not rid:
            return ToolResult("请提供提醒 ID", success=False)

        for r in _mock_reminders:
            if r["id"] == rid:
                r["status"] = "completed"
                r["completed_at"] = datetime.now().isoformat()
                return ToolResult(
                    f"✅ 已将「{r['title']}」标记为已完成。",
                    success=True,
                    metadata={"reminder": r},
                )

        return ToolResult(f"未找到 ID 为 {rid} 的提醒", success=False)

    async def _handle_delete(self, args: dict[str, Any]) -> ToolResult:
        rid = args.get("id", "").strip()
        if not rid:
            return ToolResult("请提供提醒 ID", success=False)

        for i, r in enumerate(_mock_reminders):
            if r["id"] == rid:
                removed = _mock_reminders.pop(i)
                return ToolResult(
                    f"🗑️ 已删除提醒「{removed['title']}」(ID: {rid})",
                    success=True,
                    metadata={"deleted": removed},
                )

        return ToolResult(f"未找到 ID 为 {rid} 的提醒", success=False)
