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


# 允许存进系统凭据管理器的密钥。凭据管理器优先，.env 只是回退：
# 已经把密钥存进去的用户不应该因为 .env 里还留着一份旧明文而糊涂。
_SECRET_FIELDS = ("llm_api_key",)


def _apply_keychain(target: "Settings") -> dict[str, str]:
    """用凭据管理器里的值覆盖 .env 明文，并记录每个密钥的来源。

    来源要在界面上可见，否则"我明明存进凭据管理器了，怎么还是 401"会变成
    无解的猜谜 —— 用户无法判断到底读的是哪一份。
    """
    from .secrets import get_secret

    sources: dict[str, str] = {}
    for field in _SECRET_FIELDS:
        stored = get_secret(field)
        if stored:
            object.__setattr__(target, field, stored)
            sources[field] = "keychain"
        else:
            sources[field] = "env" if getattr(target, field, "") else "missing"
    return sources


# Singleton instance
settings = Settings()
_SECRET_SOURCES = _apply_keychain(settings)


def secret_sources() -> dict[str, str]:
    """每个受保护密钥当前实际来自哪里：keychain / env / missing。"""
    return dict(_SECRET_SOURCES)


def reload_secrets() -> dict[str, str]:
    """凭据变动后重新解析，并刷新 secret_sources() 读到的那份缓存。"""
    global _SECRET_SOURCES
    _SECRET_SOURCES = _apply_keychain(settings)
    return dict(_SECRET_SOURCES)
