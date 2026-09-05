"""Skill center for JARVIS — reusable prompt templates exposed as clickable skills.

Phase 16 P2 implementation. Skills are declarative prompt templates that can be
triggered from the UI or by name. They are not tools; they produce a user message
that is sent through the normal chat flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    id: str
    name: str
    description: str
    icon: str
    category: str
    prompt_template: str
    requires_input: bool = False
    input_label: str = ""
    input_placeholder: str = ""
    default_input: str = ""


# ------------------------------------------------------------------
# Built-in skills
# ------------------------------------------------------------------

BUILTIN_SKILLS: list[Skill] = [
    Skill(
        id="morning_digest",
        name="每日简报",
        description="生成包含时间、天气、日程和待办的每日简报",
        icon="🌅",
        category="效率",
        prompt_template="请帮我生成今天的每日简报。包括：当前时间、天气、今日日程、待办事项。如果有定时任务系统，请尝试调用相关工具获取信息。",
    ),
    Skill(
        id="code_review",
        name="代码审查",
        description="审查工作区中的代码文件或指定代码片段",
        icon="🔍",
        category="开发",
        prompt_template="请对以下代码进行审查，关注：潜在 bug、性能问题、可读性、安全风险和改进建议。\n\n```{language}\n{input}\n```",
        requires_input=True,
        input_label="代码片段",
        input_placeholder="粘贴要审查的代码...",
    ),
    Skill(
        id="screen_assistant",
        name="屏幕助手",
        description="截图并解释当前屏幕内容",
        icon="🖥️",
        category="系统",
        prompt_template="请使用截图工具查看当前屏幕，并告诉我屏幕上显示了什么。如果用户有具体问题，请优先回答：{input}",
        requires_input=True,
        input_label="想问什么（可选）",
        input_placeholder="例如：这个报错是什么意思？",
        default_input="",
    ),
    Skill(
        id="summarize_webpage",
        name="总结网页",
        description="输入网址，自动抓取并总结网页内容",
        icon="🌐",
        category="效率",
        prompt_template="请打开网页 {input}，提取主要内容并生成简洁的中文总结。包括：标题、核心观点、关键信息和结论。",
        requires_input=True,
        input_label="网页 URL",
        input_placeholder="https://...",
    ),
    Skill(
        id="translate",
        name="翻译",
        description="翻译选中文本或输入文本",
        icon="🌐",
        category="效率",
        prompt_template="请将以下内容翻译成自然流畅的中文（如果是中文则翻译成英文）：\n\n{input}",
        requires_input=True,
        input_label="要翻译的文本",
        input_placeholder="粘贴文本...",
    ),
    Skill(
        id="explain_code",
        name="解释代码",
        description="用通俗语言解释代码片段",
        icon="💡",
        category="开发",
        prompt_template="请用通俗易懂的语言解释以下代码，说明它的作用、关键步骤和注意事项：\n\n```{language}\n{input}\n```",
        requires_input=True,
        input_label="代码片段",
        input_placeholder="粘贴要解释的代码...",
    ),
    Skill(
        id="write_email",
        name="写邮件",
        description="根据要点生成正式邮件",
        icon="✉️",
        category="办公",
        prompt_template="请根据以下要点帮我写一封正式、礼貌的中文邮件。要求：主题明确、称呼得体、条理清晰、结尾礼貌。\n\n要点：{input}",
        requires_input=True,
        input_label="邮件要点",
        input_placeholder="输入邮件要点...",
    ),
    Skill(
        id="meeting_minutes",
        name="会议纪要",
        description="将会议记录整理为结构化纪要",
        icon="📝",
        category="办公",
        prompt_template="请将以下会议记录整理为结构化会议纪要，包括：会议主题、时间、参会人、讨论要点、决议事项、待办任务（含负责人和截止日期）。\n\n会议记录：\n{input}",
        requires_input=True,
        input_label="会议记录",
        input_placeholder="粘贴会议记录...",
    ),
]


_SKILL_MAP: dict[str, Skill] = {s.id: s for s in BUILTIN_SKILLS}


def get_skills() -> list[dict[str, Any]]:
    """Return all skills as serializable dicts."""
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "icon": s.icon,
            "category": s.category,
            "requires_input": s.requires_input,
            "input_label": s.input_label,
            "input_placeholder": s.input_placeholder,
            "default_input": s.default_input,
        }
        for s in BUILTIN_SKILLS
    ]


def get_skill(skill_id: str) -> Skill | None:
    """Get a skill by ID."""
    return _SKILL_MAP.get(skill_id)


def render_skill_prompt(skill_id: str, input_text: str = "", language: str = "") -> str:
    """Render a skill prompt template with the given input."""
    skill = _SKILL_MAP.get(skill_id)
    if skill is None:
        raise ValueError(f"Unknown skill: {skill_id}")

    prompt = skill.prompt_template
    prompt = prompt.replace("{input}", input_text or skill.default_input)
    prompt = prompt.replace("{language}", language or "")
    return prompt
