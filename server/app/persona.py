"""JARVIS persona & system-prompt management."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import database as db
from .config import settings
from .models import PersonaConfig

logger = logging.getLogger("jarvis")

# DB keys used to persist persona / model overrides
_PERSONA_KEY = "persona_config"
_MODEL_KEY = "model_config"

_SYSTEM_PROMPT_TEMPLATE = """\
你是 {name}，{owner_title} 的私人 AI 助手。
你的语气风格是 {tone}，回复简洁有力。
你通过系统提示词获取当前时间和可用能力信息。

## 对话规则
1. 回复使用中文，除非用户使用其他语言
2. 回复简洁有力，避免冗余
3. 如果不确定，坦诚告知
4. 保持友好但不失专业

## 可用能力（工具）
当用户的问题需要以下能力时，你必须使用对应的工具：
- **web_search**：搜索互联网获取最新信息（新闻、天气、事实、价格等）。当用户问"现在/今天/最新"相关的问题时，必须先搜索。
- **web_fetch**：抓取网页内容。当需要阅读某个 URL 的完整内容时使用。
- **code_execution**：执行 Python 代码。当用户需要计算、数据处理、编程等任务时使用。
- **memory**：搜索或保存记忆。记住用户告诉你的重要信息。
- **calculator**：数学计算。
- **system_info**：查询系统信息（时间、日期等）。
- **calendar**：管理日程。
- **reminder**：管理提醒/待办。
- **weather**：查询天气。
- **clipboard**：剪贴板操作。
- **system_monitor**：系统资源监控。
- **screenshot**：截图。当用户要求识别、提取或读取屏幕上的文字时，必须将 ocr 参数设为 true。
- **system_control**：系统控制。用于打开/关闭应用程序、调节音量等。
- **browser_open / browser_extract / browser_screenshot / browser_click / browser_close**：浏览器自动化。
- **workspace_open / workspace_list_files / workspace_search / workspace_read_file / workspace_write_file / workspace_run_command**：项目管理。workspace_open 不传 path 时自动打开用户主目录。
- **vision_describe**：截图并返回屏幕结构化描述（UI 元素、文字、图表）。用于"看看当前屏幕"类问题。
- **vision_agent**：视觉 Agent，根据目标自动截图→规划→点击/输入/滚动，直到完成。用于桌面自动化任务（打开应用、填表、操作 GUI）。
  ⚠️ 桌面自动化任务（"打开记事本写内容"、"截图"）优先使用 vision_agent，不要使用 workspace 工具。

### 工具调用格式
在回复中需要调用工具时，输出以下 JSON 格式（可以混合在文字中）：
TOOL_CALL_EXAMPLE

当前时间：{now}"""

# JSON example injected after template rendering to avoid brace conflicts
_TOOL_CALL_EXAMPLE = '{"tool": "web_search", "args": {"query": "搜索内容"}}'


def _extract_last_user_text(messages: list[dict]) -> str | None:
    """Return the text of the most recent non-system user message."""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                return " ".join(parts)
    return None


async def build_system_prompt(
    persona: PersonaConfig | None = None,
    include_memories: bool = True,
    messages: list[dict] | None = None,
) -> str:
    """Build the system prompt for the LLM call.

    If *persona* is ``None`` the default settings are used.
    If *include_memories* is True, relevant memories are appended.
    *messages* is used to semantically retrieve the most relevant memories.
    """
    if persona is None:
        persona = PersonaConfig(
            name=settings.persona_name,
            tone=settings.persona_tone,
            owner_title=settings.persona_owner_title,
            self_title=settings.persona_self_title,
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        name=persona.name,
        tone=persona.tone,
        owner_title=persona.owner_title,
        self_title=persona.self_title,
        now=now,
    ).replace("TOOL_CALL_EXAMPLE", _TOOL_CALL_EXAMPLE)

    if include_memories:
        try:
            from .memory import get_context_memories
            query = _extract_last_user_text(messages) if messages else None
            memories = await get_context_memories(limit=5, query=query)
            if memories:
                prompt += f"\n\n## 关于用户的记忆\n{memories}"
        except Exception:
            pass  # Don't fail if memory system not ready

    return prompt


# ------------------------------------------------------------------
# DB-backed persona helpers
# ------------------------------------------------------------------

async def get_persona_config() -> PersonaConfig:
    """Load persona config from the DB, falling back to env / defaults."""
    raw = await db.get_setting(_PERSONA_KEY)
    if raw is not None:
        try:
            return PersonaConfig.model_validate_json(raw)
        except Exception:
            logger.warning("Failed to parse stored persona config, using defaults")
    return PersonaConfig(
        name=settings.persona_name,
        tone=settings.persona_tone,
        owner_title=settings.persona_owner_title,
        self_title=settings.persona_self_title,
    )


async def save_persona_config(persona: PersonaConfig) -> None:
    await db.set_setting(_PERSONA_KEY, persona.model_dump_json())
