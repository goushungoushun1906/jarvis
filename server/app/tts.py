"""TTS service：edge-tts（在线）为主，Windows 本地语音（SAPI）为兜底。

为什么要兜底：edge-tts 走的是微软在线接口，一旦被拒（实测返回 403 Invalid
response status）或断网，整条播报链路就是「AI 回答了，但一句话都听不见」——
而且故障点在服务端，用户端看不出任何区别。本地 SAPI 是系统自带、离线可用，
音色差一点，但保证「永远有声音」。

引擎选择：JARVIS_TTS_ENGINE=auto（默认，在线优先、失败自动落本地）
          / edge（只用在线）/ sapi（只用本地，完全离线）
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import edge_tts

from app.config import settings

logger = logging.getLogger(__name__)

# auto：先在线后本地；edge：只在线；sapi：只本地（离线）
TTS_ENGINE = (os.environ.get("JARVIS_TTS_ENGINE", "").strip().lower() or "auto")

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
# 本地兜底：Windows SAPI（离线）
# ---------------------------------------------------------------------------

# edge 的 voice id → SAPI 语音描述里该有的关键词。系统装了什么就是什么，
# 所以只能按语言挑，不能强求音色一致 —— 有声音永远比音色好听重要。
_SAPI_LANG_HINTS = {
    "zh": ("Chinese", "中文", "简体"),
    "en": ("English",),
}


def _sapi_pick_voice(speaker, voice: str):
    """按目标语言挑一个已安装的 SAPI 语音，挑不到就用系统默认。"""
    lang = "zh" if voice.lower().startswith("zh") else "en"
    hints = _SAPI_LANG_HINTS.get(lang, ())
    try:
        for candidate in speaker.GetVoices():
            desc = candidate.GetDescription()
            if any(h in desc for h in hints):
                return candidate
    except Exception as e:
        logger.warning("枚举 SAPI 语音失败：%s", e)
    return None


def synthesize_sapi_bytes(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """用 Windows 系统语音合成，返回 WAV bytes。

    只在 Windows 有效；其他平台抛错，由上层继续按在线失败处理。
    """
    import win32com.client

    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    picked = _sapi_pick_voice(speaker, voice)
    if picked is not None:
        speaker.Voice = picked

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(tmp_path, 3)  # SSFMCreateForWrite
        speaker.AudioOutputStream = stream
        # 0 = 同步说完再返回，否则文件还没写完就被读走
        speaker.Speak(text, 0)
        stream.Close()

        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise RuntimeError("SAPI 合成为空")
        return data
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def synthesize(
    text: str, voice: str = DEFAULT_VOICE
) -> tuple[bytes, str]:
    """统一入口：返回 (音频字节, media_type)。

    在线失败时**必须**落本地而不是报错 —— 用户感知到的只是「没声音」，
    而播报是这个应用的出口，静默失败等于对话断在最后一步。
    """
    engine = TTS_ENGINE

    if engine != "sapi":
        try:
            return await synthesize_to_bytes(text, voice), "audio/mpeg"
        except Exception as e:
            if engine == "edge":
                raise
            logger.warning("在线语音合成失败，改用本地语音：%s", e)

    # 阻塞的 COM 调用丢到线程里，别卡住事件循环
    data = await asyncio.to_thread(synthesize_sapi_bytes, text, voice)
    return data, "audio/wav"


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
