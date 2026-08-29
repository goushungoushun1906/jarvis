"""Clipboard plugin — clipboard history management with mock data storage."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")


# ── Mock clipboard history ────────────────────────────────────────────
# In a real implementation, this would interact with the system clipboard
# and persist history to a database.

def _generate_mock_history() -> list[dict]:
    """Generate sample clipboard entries for demonstration."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": str(uuid.uuid4())[:8],
            "content": "https://github.com/jarvis-project/jarvis-core/pull/142",
            "label": "PR link",
            "timestamp": now.isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "content": "import React, { useState, useEffect } from 'react';\n\nconst useDebounce = (value: string, delay: number): string => {\n  const [debouncedValue, setDebouncedValue] = useState(value);\n  useEffect(() => {\n    const handler = setTimeout(() => setDebouncedValue(value), delay);\n    return () => clearTimeout(handler);\n  }, [value, delay]);\n  return debouncedValue;\n};",
            "label": "useDebounce hook",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "content": "Q3 项目里程碑：\n- 7月15日：完成用户认证模块重构\n- 8月1日：上线新版数据看板\n- 8月20日：API v2 迁移完成\n- 9月10日：全平台性能优化发布",
            "label": "Q3 milestones",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "content": "ssh -i ~/.ssh/prod_key.pem ubuntu@10.0.1.42",
            "label": "SSH prod server",
            "timestamp": (now - timedelta(days=1)).isoformat(),
        },
        {
            "id": str(uuid.uuid4())[:8],
            "content": "贵司的方案我们已收到，整体技术架构很清晰，有几个细节需要进一步沟通：\n1. 数据迁移的时间窗口是否可以在周末进行？\n2. SLA 保障级别需要明确到 99.9% 还是 99.99%？\n3. 第三阶段的交付物清单请补充验收标准。\n\n下周二下午 3 点方便电话沟通吗？",
            "label": "客户回复邮件",
            "timestamp": (now - timedelta(days=2)).isoformat(),
        },
    ]


class ClipboardPlugin(BasePlugin):
    """A plugin that provides clipboard history management."""

    _history: list[dict] = []

    async def on_load(self) -> None:
        self._history = _generate_mock_history()
        logger.info(
            "ClipboardPlugin v%s loaded with %d mock clipboard entries",
            self.version,
            len(self._history),
        )

    async def on_enable(self) -> None:
        logger.info("ClipboardPlugin enabled — mock clipboard ready")

    async def on_disable(self) -> None:
        logger.info("ClipboardPlugin disabled")

    async def on_unload(self) -> None:
        logger.info("ClipboardPlugin unloaded")

    # ── Command dispatch ───────────────────────────────────────────────

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch command by name."""
        if command_name == "get_clipboard":
            return await self._handle_get(args)
        if command_name == "set_clipboard":
            return await self._handle_set(args)
        if command_name == "list_history":
            return await self._handle_list(args)
        if command_name == "clear_history":
            return await self._handle_clear(args)
        if command_name == "search_history":
            return await self._handle_search(args)
        return ToolResult(
            f"Unknown command: {command_name}", success=False
        )

    # ── Command handlers ───────────────────────────────────────────────

    async def _handle_get(self, args: dict[str, Any]) -> ToolResult:
        """Return the most recent clipboard entry."""
        del args  # no parameters needed
        if not self._history:
            return ToolResult(
                "剪贴板历史为空。",
                success=True,
                metadata={"entry": None, "total": 0},
            )

        latest = self._history[0]
        return ToolResult(
            content=f"📋 最近剪贴板内容 [{latest['id']}]：\n\n{latest['content']}\n\n"
            f"标签: {latest.get('label', '无')}\n"
            f"时间: {latest['timestamp']}",
            success=True,
            metadata={"entry": latest, "total": len(self._history)},
        )

    async def _handle_set(self, args: dict[str, Any]) -> ToolResult:
        """Add a new entry to clipboard history."""
        content = args.get("content", "").strip()
        label = args.get("label", "").strip()

        if not content:
            return ToolResult("剪贴板内容不能为空", success=False)

        # Read max_history_size from config
        config = await self.get_config()
        max_size = int(config.get("max_history_size", 50))

        entry = {
            "id": str(uuid.uuid4())[:8],
            "content": content,
            "label": label or None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Insert at the front (most recent first)
        self._history.insert(0, entry)

        # Trim to max_history_size
        if len(self._history) > max_size:
            trimmed = self._history[max_size:]
            self._history = self._history[:max_size]
            logger.debug(
                "Trimmed %d old clipboard entries (limit: %d)",
                len(trimmed),
                max_size,
            )

        preview = content[:60] + ("..." if len(content) > 60 else "")
        lines = [
            f"✅ 剪贴板内容已保存",
            f"ID: {entry['id']}",
            f"标签: {entry['label'] or '无'}",
            f"内容预览: {preview}",
            f"当前历史条目数: {len(self._history)}",
        ]

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={
                "entry": entry,
                "total": len(self._history),
            },
        )

    async def _handle_list(self, args: dict[str, Any]) -> ToolResult:
        """List clipboard history entries."""
        limit = int(args.get("limit", 10))

        if not self._history:
            return ToolResult(
                "剪贴板历史为空。",
                success=True,
                metadata={"entries": [], "total": 0},
            )

        entries = self._history[:limit]

        lines = [f"📋 剪贴板历史（共 {len(self._history)} 条，显示前 {len(entries)} 条）：\n"]
        for i, entry in enumerate(entries, 1):
            content = entry["content"]
            preview = content[:60].replace("\n", " ") + ("..." if len(content) > 60 else "")
            label = entry.get("label") or "无"
            lines.append(
                f"{i}. [{entry['id']}] {label}\n"
                f"   {preview}\n"
                f"   时间: {entry['timestamp']}"
            )
            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={
                "entries": entries,
                "total": len(self._history),
                "limit": limit,
            },
        )

    async def _handle_clear(self, args: dict[str, Any]) -> ToolResult:
        """Clear all clipboard history entries."""
        del args  # no parameters needed
        removed_count = len(self._history)
        self._history.clear()

        return ToolResult(
            content=f"✅ 已清空所有剪贴板历史（共 {removed_count} 条）。",
            success=True,
            metadata={"removed": removed_count},
        )

    async def _handle_search(self, args: dict[str, Any]) -> ToolResult:
        """Search clipboard history by keyword."""
        keyword = args.get("keyword", "").strip().lower()

        if not keyword:
            return ToolResult("搜索关键词不能为空", success=False)

        matching = []
        for entry in self._history:
            content_lower = entry["content"].lower()
            label_lower = (entry.get("label") or "").lower()
            if keyword in content_lower or keyword in label_lower:
                matching.append(entry)

        if not matching:
            return ToolResult(
                f"没有找到包含关键词「{args.get('keyword', '')}」的剪贴板条目。",
                success=True,
                metadata={"count": 0, "keyword": args.get("keyword", "")},
            )

        lines = [f"🔍 找到 {len(matching)} 条匹配「{args.get('keyword', '')}」的剪贴板记录：\n"]
        for i, entry in enumerate(matching, 1):
            content = entry["content"]
            preview = content[:60].replace("\n", " ") + ("..." if len(content) > 60 else "")
            label = entry.get("label") or "无"
            lines.append(
                f"{i}. [{entry['id']}] {label}\n"
                f"   {preview}\n"
                f"   时间: {entry['timestamp']}"
            )
            lines.append("")

        return ToolResult(
            content="\n".join(lines),
            success=True,
            metadata={
                "entries": matching,
                "count": len(matching),
                "keyword": args.get("keyword", ""),
            },
        )
