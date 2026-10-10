"""JARVIS Pydantic models for request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# --- Chat ---

class ImageAttachment(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: dict  # {"url": "data:image/xxx;base64,..."}
    filename: str = ""


class FileAttachment(BaseModel):
    type: Literal["file"] = "file"
    filename: str
    mime_type: str = ""
    extracted_text: str = ""


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    images: list[ImageAttachment] = Field(default_factory=list)
    files: list[FileAttachment] = Field(default_factory=list)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = True
    conversation_id: str | None = None


# --- Conversations ---

class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str
    message_count: int
    parent_id: str | None = None
    forked_from_message_id: int | None = None


class ForkRequest(BaseModel):
    message_id: int
    new_title: str | None = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


# --- Config ---

class PersonaConfig(BaseModel):
    name: str = "JARVIS"
    tone: str = "professional"
    owner_title: str = "Boss"
    self_title: str = "your assistant"


class ModelConfig(BaseModel):
    model: str
    base_url: str


# --- Health ---

class HealthResponse(BaseModel):
    status: str
    model: str
    base_url: str
    # 构建标识。Electron 启动时用它判断"已经在 18200 上跑着的后端是不是
    # 当前这份代码"——不匹配就说明是旧版本的残留进程，必须换掉，否则
    # 用户换了新包却仍在跟旧后端对话，界面上完全看不出来。
    build: str = "unknown"


# --- Model Profiles ---

Tier = Literal["fast", "mid", "deep"]

# Default tier mapping for built-in profiles.
DEFAULT_TIER_MAP: dict[str, Tier] = {
    "agnes-flash": "fast",
    "agnes-15": "mid",
    "gpt4o-mini": "fast",
    "gpt4o": "mid",
    "deepseek": "deep",
    "doubao": "mid",
    "deepseek-r1-ollama": "deep",
    "claude": "deep",
}


class ModelProfile(BaseModel):
    """A saved model configuration that can be quickly switched to."""
    id: str  # unique slug like "agnes-flash", "gpt4o", "deepseek"
    name: str  # display name like "Agnes Flash", "GPT-4o"
    model: str  # model ID for API call like "agnes-2.0-flash"
    base_url: str  # API base URL
    api_key: str = ""  # if empty, use the global default API key
    provider: str = "custom"  # provider name for grouping
    is_active: bool = False
    tier: Tier = "mid"  # fast / mid / deep routing tier


class ModelProfileUpdate(BaseModel):
    """Partial update for a saved model profile."""
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None
    tier: Tier | None = None


class ModelValidateRequest(BaseModel):
    """Request to check whether a model id is accepted by a gateway.

    ``base_url``/``api_key`` may be omitted when ``profile_id`` is supplied —
    they are then resolved from the saved profile.
    """
    base_url: str = ""
    model: str
    api_key: str = ""  # if empty, fall back to the profile's / global key
    profile_id: str | None = None  # resolve a missing base_url/api_key from here


class SwitchModelRequest(BaseModel):
    profile_id: str  # which preset to switch to
