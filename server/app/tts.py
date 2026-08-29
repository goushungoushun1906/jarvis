"""TTS service using edge-tts."""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from pathlib import Path

import edge_tts

from app.config import settings

# ---------------------------------------------------------------------------
# Available voices
# ---------------------------------------------------------------------------

VOICES = {
    "zh-CN-XiaoxiaoNeural": {"lang": "zh-CN", "gender": "Female", "desc": "晓晓 - 温暖女声"},
    "zh-CN-YunxiNeural": {"lang": "zh-CN", "gender": "Male", "desc": "云希 - 阳光男声"},
    "zh-CN-YunjianNeural": {"lang": "zh-CN", "gender": "Male", "desc": "云健 - 成熟男声"},
    "zh-CN-XiaoyiNeural": {"lang": "zh-CN", "gender": "Female", "desc": "晓依 - 知性女声"},
    "zh-CN-YunyangNeural": {"lang": "zh-CN", "gender": "Male", "desc": "云扬 - 新闻男声"},
    "en-US-JennyNeural": {"lang": "en-US", "gender": "Female", "desc": "Jenny - English Female"},
    "en-US-GuyNeural": {"lang": "en-US", "gender": "Male", "desc": "Guy - English Male"},
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

async def synthesize_to_file(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_dir: str | None = None,
) -> str:
    """Synthesize text to a temp MP3 file, return file path."""
    communicate = edge_tts.Communicate(text, voice)

    if output_dir is None:
        output_dir = tempfile.gettempdir()

    output_path = os.path.join(output_dir, f"jarvis_tts_{id(text) % 100000}.mp3")
    await communicate.save(output_path)
    return output_path


async def synthesize_to_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
) -> bytes:
    """Synthesize text to MP3 bytes."""
    communicate = edge_tts.Communicate(text, voice)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    await communicate.save(tmp_path)
    with open(tmp_path, "rb") as f:
        data = f.read()
    os.unlink(tmp_path)
    return data


# ---------------------------------------------------------------------------
# Voice listing
# ---------------------------------------------------------------------------

async def list_voices() -> list[dict]:
    """Return available voice list with metadata."""
    return [
        {"id": vid, **vmeta}
        for vid, vmeta in VOICES.items()
    ]


async def get_voices_by_lang(lang: str = "zh-CN") -> list[str]:
    """Return voice IDs for a given language."""
    return [vid for vid, vmeta in VOICES.items() if vmeta["lang"] == lang]
