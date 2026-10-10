"""Local speech-to-text module.

Strategy order:
  1. faster-whisper (OpenAI Whisper via CTranslate2) — best accuracy
  2. Vosk (fallback) — offline, lightweight
"""
from __future__ import annotations
import os
import sys
import io
import wave
import logging
import threading
import struct
import tempfile
import numpy as np

logger = logging.getLogger("jarvis")

# ─── 模型目录定位 ──────────────────────────────────────────────────────
#
# 这里必须处理打包（sys.frozen）场景：此时 __file__ 指向 PyInstaller 的
# _MEIPASS 临时解压目录，而 models/ 只有被打进 exe 才会在那里。
# 打包版 STT 静默失效的真正原因就是这个 —— 模型没打包进去，路径查不到，
# 于是每次都回落到网络 Whisper，表现为"14 秒后返回空"。
#
# 优先级（先命中先赢）：
#   1. JARVIS_STT_MODEL_DIR 环境变量（排查 / 自定义模型位置）
#   2. 打包态：exe 所在目录/models —— 外部资源，用户可放 2GB 大模型覆盖
#   3. 打包态：_MEIPASS/models —— 打进 exe 的 small 模型（开箱即用）
#   4. 开发态：<server>/models
def _candidate_model_roots() -> list[str]:
    roots: list[str] = []

    # 显式指定即"覆盖"，不回退到默认位置：用户既然指定了目录，静默回退到
    # 另一个模型只会让"为什么换模型没生效"变成猜谜。路径写错时宁可直接
    # 报不可用（日志会打出搜索过的路径）。
    env_root = os.environ.get("JARVIS_STT_MODEL_DIR")
    if env_root:
        return [env_root]

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots.append(os.path.join(exe_dir, "models"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(os.path.join(meipass, "models"))
    else:
        roots.append(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models",
            )
        )
    return roots


def _find_model_dir(*subpaths: str) -> str | None:
    """在候选根目录里找第一个真实存在的模型子目录。"""
    for root in _candidate_model_roots():
        candidate = os.path.join(root, *subpaths)
        if os.path.isdir(candidate):
            return candidate
    return None


def _default_vosk_dir() -> str | None:
    """定位 Vosk 模型目录。

    优先级：
      1. JARVIS_STT_MODEL —— 精确到模型目录，用于在小/大模型间强制切换
      2. 小模型（66MB，加载快，默认打包）
      3. 大模型（2GB，准确率高，需用户自行放到 exe 同级 models/vosk/）
    """
    forced = os.environ.get("JARVIS_STT_MODEL")
    if forced and os.path.isdir(forced):
        return forced

    return (
        _find_model_dir("vosk", "vosk-model-small-cn-0.22")
        or _find_model_dir("vosk", "vosk-model-cn-0.22")
    )


def _default_whisper_dir() -> str | None:
    return _find_model_dir("whisper-tiny")


# ─── faster-whisper (preferred) ─────────────────────────────────────────
_whisper_model = None
_whisper_lock = threading.Lock()
_WHISPER_MODEL_SIZE = "tiny"  # tiny=75MB, base=150MB, small=500MB
_WHISPER_MODEL_DIR = _default_whisper_dir() or ""
_WHISPER_LOADED = False
_WHISPER_AVAILABLE = False  # set True if import succeeds

# ─── Vosk (fallback) ──────────────────────────────────────────────────
_MODEL_PATH = _default_vosk_dir() or ""

_vosk_model = None
_vosk_lock = threading.Lock()


def _load_whisper_model():
    """Lazily load faster-whisper model."""
    global _whisper_model, _WHISPER_LOADED, _WHISPER_AVAILABLE

    if _WHISPER_LOADED:
        return _whisper_model

    with _whisper_lock:
        if _WHISPER_LOADED:
            return _whisper_model

        _WHISPER_LOADED = True

        model_path = _default_whisper_dir()
        if not model_path:
            # 没有本地模型时 WhisperModel("tiny") 会去 HuggingFace 现场下载 ——
            # 在离线/打包环境里这不是"慢"，而是直接把语音链路卡成超时。
            # 只有显式开 JARVIS_STT_ALLOW_DOWNLOAD 才允许联网取模型。
            if os.environ.get("JARVIS_STT_ALLOW_DOWNLOAD") != "1":
                logger.info(
                    "faster-whisper 本地模型不存在且未开启 JARVIS_STT_ALLOW_DOWNLOAD，跳过"
                )
                return None
            model_path = _WHISPER_MODEL_SIZE

        try:
            from faster_whisper import WhisperModel

            logger.info("Loading faster-whisper model from '%s' (CPU, int8)...", model_path)
            _whisper_model = WhisperModel(
                model_path,
                device="cpu",
                compute_type="int8",
            )
            _WHISPER_AVAILABLE = True
            logger.info("faster-whisper model loaded successfully")
            return _whisper_model
        except Exception as e:
            logger.warning("faster-whisper not available: %s", e)
            _WHISPER_AVAILABLE = False
            return None


def _load_vosk_model():
    """Lazily load Vosk model."""
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model

    with _vosk_lock:
        if _vosk_model is not None:
            return _vosk_model

        model_path = _default_vosk_dir()
        if not model_path:
            # 打包版最常见的失败原因：models/ 没被打进 exe。把搜索过的路径
            # 全打出来，否则日志里只有一句 "Vosk model not found"，排查成本很高。
            logger.warning(
                "Vosk 模型未找到。已搜索: %s（可用 JARVIS_STT_MODEL_DIR 指定，"
                "或把模型放到 exe 同级 models/vosk/ 下）",
                _candidate_model_roots(),
            )
            return None

        try:
            from vosk import Model as VoskModel
            _vosk_model = VoskModel(model_path)
            logger.info("Vosk 模型已加载: %s", model_path)
            return _vosk_model
        except Exception as e:
            logger.error("Failed to load Vosk model: %s", e)
            return None


def is_model_available() -> bool:
    """是否真的有可用的**离线**模型。

    注意不要把"能 import faster_whisper"当成可用：那样 WhisperModel 会去
    HuggingFace 现场下载模型，在无网/打包环境里会把语音链路卡到超时。只有
    本地模型目录真的存在才算数。
    """
    if _default_vosk_dir():
        return True
    if _default_whisper_dir():
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def model_status() -> dict:
    """供 /api/voice/stt-status 与排查使用的模型可见性信息。"""
    vosk_dir = _default_vosk_dir()
    whisper_dir = _default_whisper_dir()
    return {
        "available": is_model_available(),
        "vosk_model": vosk_dir,
        "whisper_model": whisper_dir,
        "search_roots": _candidate_model_roots(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _transcribe_whisper(pcm_data: bytes, sample_rate: int) -> str | None:
    """Transcribe using faster-whisper. Returns None if not available."""
    model = _load_whisper_model()
    if model is None:
        return None

    try:
        # faster-whisper needs a numpy float32 array
        audio_np = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(
            audio_np,
            language="zh",
            beam_size=5,
            vad_filter=False,
        )

        text_parts = []
        for seg in segments:
            if seg.text and seg.text.strip():
                text_parts.append(seg.text.strip())

        result = " ".join(text_parts).strip()
        logger.info("STT (faster-whisper): '%s'", result)
        return result if result else None
    except Exception as e:
        logger.error("faster-whisper error: %s", e)
        return None


def _transcribe_vosk(pcm_data: bytes, sample_rate: int) -> str | None:
    """Transcribe using Vosk. Returns None if not available."""
    model = _load_vosk_model()
    if model is None:
        return None

    try:
        from vosk import KaldiRecognizer
        import json

        rec = KaldiRecognizer(model, sample_rate)
        rec.SetWords(True)

        text_parts = []
        chunk_size = 4000
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i:i + chunk_size]
            if not chunk:
                break
            if rec.AcceptWaveform(chunk):
                parsed = json.loads(rec.Result())
                if parsed.get("text"):
                    text_parts.append(parsed["text"])

        final = json.loads(rec.FinalResult())
        if final.get("text"):
            text_parts.append(final["text"])

        result = " ".join(text_parts).strip()
        logger.info("STT (Vosk local): '%s'", result)
        return result if result else None
    except Exception as e:
        logger.error("Vosk error: %s", e)
        return None


def _hotword_fixes() -> dict[str, str]:
    """常见误识 -> 正确写法。

    small 模型（66MB）对音译专有名词识别很差：实测"贾维斯"被稳定识别成
    "假 为 为师"。2GB 大模型能识别对，但那要 25s，完全不适合语音交互。
    折中做法是保留小模型的速度，再用这张小表把明显错的产品名改回来。

    可用 JARVIS_STT_HOTWORDS 追加（格式：误识=正确,误识2=正确2）。
    """
    fixes = {
        "假为为师": "贾维斯",
        "假为师": "贾维斯",
        "假维斯": "贾维斯",
        "加维斯": "贾维斯",
        "甲维斯": "贾维斯",
        "贾维寺": "贾维斯",
    }
    extra = os.environ.get("JARVIS_STT_HOTWORDS", "")
    for pair in extra.split(","):
        pair = pair.strip()
        if "=" in pair:
            wrong, right = pair.split("=", 1)
            if wrong.strip() and right.strip():
                fixes[wrong.strip()] = right.strip()
    return fixes


def _apply_hotword_fixes(text: str) -> str:
    """把误识的专有名词改回正确写法。

    Vosk 输出带空格（"假 为 为师"），因此按"无空格串"比对，但替换发生在
    原文对应区间上 —— 这样只改动命中的那一段，其余文本和空格原样保留。
    不能直接返回去空格的串：中文模型也会识别英文，无条件去空格会把
    "hello world" 拼成 "helloworld"。
    """
    if not text:
        return text

    # 无空格串 <-> 原文位置的映射
    compact_chars: list[str] = []
    orig_pos: list[int] = []
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        compact_chars.append(ch)
        orig_pos.append(i)
    compact = "".join(compact_chars)
    if not compact:
        return text

    replacements: list[tuple[int, int, str]] = []
    for wrong, right in _hotword_fixes().items():
        start = 0
        while True:
            idx = compact.find(wrong, start)
            if idx < 0:
                break
            orig_start = orig_pos[idx]
            orig_end = orig_pos[idx + len(wrong) - 1] + 1
            replacements.append((orig_start, orig_end, right))
            start = idx + len(wrong)

    # 从后往前替换，避免前面的改动打乱后面的区间
    for orig_start, orig_end, right in sorted(replacements, reverse=True):
        text = text[:orig_start] + right + text[orig_end:]
    return text


def transcribe_wav(wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text.

    Strategy:
      1. faster-whisper (best accuracy)
      2. Vosk (fallback, lighter)

    Args:
        wav_bytes: WAV format audio (16-bit signed, mono, 16kHz preferred)

    Returns:
        Transcribed text, or empty string if no speech detected
    """
    # Extract raw PCM from WAV container
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        duration = n_frames / sample_rate

        logger.info(
            "STT input: sr=%d ch=%d bits=%d frames=%d duration=%.1fs size=%d bytes",
            sample_rate, channels, sample_width * 8, n_frames, duration, len(wav_bytes),
        )

        if channels != 1 or sample_width != 2:
            raise RuntimeError(
                f"Unsupported WAV format: channels={channels}, bits={sample_width*8}. "
                f"Need 1 channel, 16-bit."
            )

        pcm_data = wf.readframes(wf.getnframes())

    # Analyze audio energy
    samples = struct.unpack(f"<{len(pcm_data)//2}h", pcm_data)
    max_amp = max(abs(s) for s in samples) if samples else 0
    avg_amp = sum(abs(s) for s in samples) / len(samples) if samples else 0
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0
    logger.info("STT audio energy: max=%d avg=%.1f rms=%.1f", max_amp, avg_amp, rms)

    # Normalize audio to prevent clipping distortion
    if rms > 16000:
        target_rms = 8000
        scale = target_rms / rms
        logger.info("STT normalizing audio: scale=%.2f", scale)
        pcm_data = struct.pack(
            f"<{len(samples)}h",
            *(max(-32768, min(32767, int(s * scale))) for s in samples)
        )

    # Strategy 1: faster-whisper
    result = _transcribe_whisper(pcm_data, sample_rate)
    if result:
        return _apply_hotword_fixes(result)

    # Strategy 2: Vosk fallback
    result = _transcribe_vosk(pcm_data, sample_rate)
    if result:
        return _apply_hotword_fixes(result)

    return ""
