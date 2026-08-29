"""JARVIS application configuration using Pydantic v2 Settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the server/ directory.
# In PyInstaller production (sys.frozen), the .env is alongside the exe.
# We try: (1) same dir as this file going up to server/, (2) exe parent dir, (3) cwd.
if getattr(sys, 'frozen', False):
    _meipass = getattr(sys, '_MEIPASS', None)
    if _meipass:
        _candidate_dirs = [Path(_meipass), Path.cwd()]
    else:
        _candidate_dirs = [Path(sys.executable).parent, Path.cwd()]
else:
    _candidate_dirs = []

# Resolve .env: prefer directory of this file (server/), fallback to candidates
_server_dir = Path(__file__).resolve().parent.parent
_env_path = _server_dir / ".env"
if not _env_path.exists():
    for _d in _candidate_dirs:
        _candidate_env = _d / ".env"
        if _candidate_env.exists():
            _env_path = _candidate_env
            break

# Also update database.py's expected location: store the resolved server dir
os.environ['_JARVIS_SERVER_DIR'] = str(_env_path.parent)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_env_path),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM settings
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 300.0          # seconds for non-streaming LLM calls
    llm_stream_timeout: float = 600.0   # seconds for streaming LLM calls
    server_port: int = 18200

    # Persona settings
    persona_name: str = "JARVIS"
    persona_tone: str = "professional"
    persona_owner_title: str = "Boss"
    persona_self_title: str = "your assistant"

    # TTS settings
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # Chat settings
    max_context_messages: int = 20
    max_tokens_context: int = 4000


# Singleton instance
settings = Settings()
