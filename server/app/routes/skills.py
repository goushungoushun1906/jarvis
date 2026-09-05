"""Skills API — list and invoke reusable prompt templates."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..skills import get_skills, render_skill_prompt

router = APIRouter(prefix="/api/skills", tags=["skills"])


class InvokeSkillRequest(BaseModel):
    skill_id: str
    input: str = ""
    language: str = ""


@router.get("")
async def list_skills():
    """List all available skills."""
    return {"skills": get_skills()}


@router.post("/invoke")
async def invoke_skill(request: InvokeSkillRequest):
    """Render a skill prompt without sending it to the chat engine.

    The frontend receives the rendered prompt and can insert it into the
    active conversation as a user message.
    """
    try:
        prompt = render_skill_prompt(request.skill_id, request.input, request.language)
        return {"success": True, "prompt": prompt}
    except ValueError as e:
        return {"success": False, "error": str(e)}
