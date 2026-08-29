"""Local speech-to-text module.

Strategy order:
  1. faster-whisper (OpenAI Whisper via CTranslate2) — best accuracy
  2. Vosk (fallback) — offline, lightweight
"""
from __future__ import annotations
import os
import io
import wave
import logging
import threading
import struct
import tempfile
import numpy as np

logger = logging.getLogger("jarvis")

# ─── faster-whisper (preferred) ─────────────────────────────────────────
_whisper_model = None
_whisper_lock = threading.Lock()
_WHISPER_MODEL_SIZE = "tiny"  # tiny=75MB, base=150MB, small=500MB
_WHISPER_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "whisper-tiny"
)
_WHISPER_LOADED = False
_WHISPER_AVAILABLE = False  # set True if import succeeds

# ─── Vosk (fallback) ──────────────────────────────────────────────────
_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "vosk")
_MODEL_LARGE = os.path.join(_MODEL_DIR, "vosk-model-cn-0.22")
_MODEL_SMALL = os.path.join(_MODEL_DIR, "vosk-model-small-cn-0.22")
_MODEL_PATH = _MODEL_SMALL if os.path.exists(_MODEL_SMALL) else _MODEL_LARGE

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
        try:
            from faster_whisper import WhisperModel

            # Use local model path if available (avoids HuggingFace download)
            model_path = _WHISPER_MODEL_DIR if os.path.isdir(_WHISPER_MODEL_DIR) else _WHISPER_MODEL_SIZE

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

        if not os.path.exists(_MODEL_PATH):
            logger.warning("Vosk model not found at %s", _MODEL_PATH)
            return None

        try:
            from vosk import Model as VoskModel
            _vosk_model = VoskModel(_MODEL_PATH)
            logger.info("Vosk model loaded from %s", _MODEL_PATH)
            return _vosk_model
        except Exception as e:
            logger.error("Failed to load Vosk model: %s", e)
            return None


def is_model_available() -> bool:
    """Check if any STT model is available."""
    if os.path.isdir(_WHISPER_MODEL_DIR):
        return True
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        return True
    except ImportError:
        pass
    return os.path.exists(_MODEL_LARGE) or os.path.exists(_MODEL_SMALL)


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
        return result

    # Strategy 2: Vosk fallback
    result = _transcribe_vosk(pcm_data, sample_rate)
    if result:
        return result

    return ""
