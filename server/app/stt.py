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


def _default_paraformer_dir() -> str | None:
    """定位 sherpa-onnx 的中文 Paraformer 模型目录。

    显式指定（JARVIS_ASR_MODEL_DIR）优先；否则在 models/asr/ 下找第一个
    同时含模型本体与词表的目录。目录名带日期版本号，写死必然踩空，所以
    是"扫目录 + 验内容"而不是拼路径。
    """
    forced = os.environ.get("JARVIS_ASR_MODEL_DIR")
    if forced and os.path.isdir(forced):
        return forced

    import glob as _glob

    for root in _candidate_model_roots():
        for candidate in sorted(_glob.glob(os.path.join(root, "asr", "*"))):
            if not os.path.isdir(candidate):
                continue
            if _glob.glob(os.path.join(candidate, "tokens.txt")) and (
                _glob.glob(os.path.join(candidate, "*.int8.onnx"))
                or _glob.glob(os.path.join(candidate, "*.onnx"))
            ):
                return candidate
    return None


def _paraformer_files(model_dir: str) -> tuple[str, str] | None:
    """返回 (模型文件, 词表)；int8 优先。"""
    import glob as _glob

    tokens = os.path.join(model_dir, "tokens.txt")
    if not os.path.isfile(tokens):
        return None
    for pattern in ("model.int8.onnx", "*.int8.onnx", "model.onnx", "*.onnx"):
        hits = _glob.glob(os.path.join(model_dir, pattern))
        if hits:
            return hits[0], tokens
    return None


# ─── faster-whisper (preferred) ─────────────────────────────────────────
_whisper_model = None
_whisper_lock = threading.Lock()
_WHISPER_MODEL_SIZE = "tiny"  # tiny=75MB, base=150MB, small=500MB
_WHISPER_MODEL_DIR = _default_whisper_dir() or ""
_WHISPER_LOADED = False
_WHISPER_AVAILABLE = False  # set True if import succeeds

# ─── sherpa-onnx Paraformer（中文首选）─────────────────────────────────
#
# 换它的原因很实在：Vosk 的 vosk-model-small-cn 是老一代 Kaldi 模型，实测把
# 「你好贾维斯」识别成「你好 假 微丝」，长句更糟。Paraformer 是新一代非自
# 回归中文模型，准确率高一档，int8 在 CPU 上也更快。而且 sherpa-onnx 已经
# 作为唤醒引擎装好了，接 ASR 不再引入新的运行时依赖。
_paraformer = None
_paraformer_lock = threading.Lock()
_PARAFORMER_LOADED = False

# ─── Vosk (fallback) ──────────────────────────────────────────────────
_MODEL_PATH = _default_vosk_dir() or ""

_vosk_model = None
_vosk_lock = threading.Lock()


def _load_paraformer():
    """懒加载 Paraformer 识别器；没有模型就安静返回 None。"""
    global _paraformer, _PARAFORMER_LOADED

    if _PARAFORMER_LOADED:
        return _paraformer

    with _paraformer_lock:
        if _PARAFORMER_LOADED:
            return _paraformer
        _PARAFORMER_LOADED = True

        model_dir = _default_paraformer_dir()
        if not model_dir:
            logger.info(
                "未找到 Paraformer 模型（models/asr/ 下为空），回落其他引擎；"
                "可用 scripts/dev/download_paraformer_asr.py 下载"
            )
            return None
        files = _paraformer_files(model_dir)
        if not files:
            logger.warning("Paraformer 模型目录不完整：%s", model_dir)
            return None
        model_file, tokens = files

        try:
            # 必须在 import sherpa_onnx 之前注册 DLL 搜索目录，否则
            # Windows 可能命中系统里那份游离的 onnxruntime（1.17.1）。
            from . import sherpa_runtime  # noqa: F401
            import sherpa_onnx

            recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=model_file,
                tokens=tokens,
                num_threads=2,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
            )
            _paraformer = recognizer
            logger.info("Paraformer 已加载：%s", model_file)
            return _paraformer
        except Exception as e:
            logger.error("Paraformer 加载失败：%s", e)
            return None


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
    if _default_paraformer_dir():
        return True
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
    paraformer_dir = _default_paraformer_dir()
    return {
        "available": is_model_available(),
        "engine": "paraformer" if paraformer_dir else ("vosk" if vosk_dir else None),
        "paraformer_model": paraformer_dir,
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


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """把样本线性重采样到目标采样率。

    Paraformer 只在 16kHz 上训练，喂别的采样率不会报错，只会输出一堆错字——
    属于"静默降级"，必须在这里显式处理掉。用线性插值而不是引入新依赖：
    语音频段内够用，且本机没有 scipy。
    """
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    n_out = max(1, int(round(len(samples) * dst_rate / src_rate)))
    src_idx = np.arange(len(samples), dtype=np.float32)
    dst_idx = np.linspace(0.0, float(len(samples) - 1), n_out, dtype=np.float32)
    return np.interp(dst_idx, src_idx, samples).astype(np.float32)


def _transcribe_paraformer(pcm_data: bytes, sample_rate: int) -> str | None:
    """用 sherpa-onnx Paraformer 转写。没有模型时返回 None 让上层回落。"""
    recognizer = _load_paraformer()
    if recognizer is None:
        return None

    try:
        samples = (
            np.frombuffer(pcm_data, dtype="<i2").astype(np.float32) / 32768.0
        )
        if sample_rate != 16000:
            samples = _resample(samples, sample_rate, 16000)

        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        text = (stream.result.text or "").strip()
        # Paraformer 的词表里带空格分词，中文不需要，去掉更自然
        text = text.replace(" ", "")
        logger.info("STT (Paraformer): '%s'", text)
        return text or None
    except Exception as e:
        logger.error("Paraformer error: %s", e)
        return None


def transcribe_wav(wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text.

    Strategy:
      1. sherpa-onnx Paraformer — 中文准确率最高（新一代非自回归模型）
      2. faster-whisper (best accuracy for mixed language)
      3. Vosk (fallback, lighter)

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

    # Strategy 1: Paraformer（中文首选）
    result = _transcribe_paraformer(pcm_data, sample_rate)
    if result:
        return result

    # Strategy 2: faster-whisper
    result = _transcribe_whisper(pcm_data, sample_rate)
    if result:
        return result

    # Strategy 3: Vosk fallback
    result = _transcribe_vosk(pcm_data, sample_rate)
    if result:
        return result

    return ""
