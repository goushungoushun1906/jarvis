"""JARVIS application configuration using Pydantic v2 Settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the server/ directory.
#
# 打包（sys.frozen）后 __file__ 指向 PyInstaller 的临时解压目录（_MEIPASS），
# 而这个目录每次启动都会重建 —— 如果把 .env / jarvis.db 解析到那里，表现就是
# "每次重启都像刚装好：对话历史、模型档位、密钥全没了"（LLM 401 就是这么来的）。
# 正确位置是 exe 所在目录（Electron 下即 resources/backend/）。
if getattr(sys, 'frozen', False):
    _exe_dir = Path(sys.executable).resolve().parent
    _meipass = getattr(sys, '_MEIPASS', None)
    _server_dir = _exe_dir
    _candidate_dirs = [_exe_dir]
    if _meipass:
        _candidate_dirs.append(Path(_meipass))
    _candidate_dirs.append(Path.cwd())
else:
    _server_dir = Path(__file__).resolve().parent.parent
    _candidate_dirs = []

# Resolve .env: prefer the server dir (dev) / exe dir (packaged), fallback to candidates
_env_path = _server_dir / ".env"
if not _env_path.exists():
    for _d in _candidate_dirs:
        _candidate_env = _d / ".env"
        if _candidate_env.exists():
            _env_path = _candidate_env
            break

# database.py / vector_memory.py 据此定位 jarvis.db 与 data/chroma_db；
# 必须指向持久化目录，不能是 _MEIPASS。
# 允许外部先设置该变量来覆盖数据目录（排查 / 迁移数据时很有用）。
os.environ.setdefault('_JARVIS_SERVER_DIR', str(_env_path.parent))


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

    # Auto-update settings (Phase 6.4)
    # 留空 = 纯离线模式，只按本地 RELEASE_NOTES 判断，不去联网。
    # 填了 URL = 启动时/检查更新时去拉远端 manifest（GitHub Release / CDN 均可）。
    update_manifest_url: str = ""
    update_check_timeout: float = 8.0  # seconds; 远端不可达时静默降级


# Singleton instance
settings = Settings()
