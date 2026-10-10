"""Voice/TTS/STT API routes."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import wave

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.tts import (
    DEFAULT_VOICE,
    list_voices,
    synthesize_to_bytes,
    synthesize_to_file,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# ── Server-side recording state ────────────────────────────────────────
_recording_state: dict = {
    "active": False,
    "frames": [],
    "start_time": 0,
}


def _get_pyaudio():
    """Lazily import and return pyaudio (avoids startup cost).

    PyAudio is a plain venv dependency now (see requirements.txt). The old
    vendored copy at ``jarvis/pyaudio_pkg`` was removed in the launcher
    clean-up — if this import fails, install it into the project venv:
    ``server\\.venv\\Scripts\\python.exe -m pip install pyaudio==0.2.14``
    """
    try:
        import pyaudio
    except ImportError as exc:  # pragma: no cover - environment problem
        raise RuntimeError(
            "pyaudio 未安装，麦克风录音不可用。请在项目 venv 执行："
            "server\\.venv\\Scripts\\python.exe -m pip install pyaudio==0.2.14"
        ) from exc
    return pyaudio


# 安静环境的本底 RMS 通常在 0.001 以下，正常说话在 0.02~0.1 之间。
# 取 0.004 作为"确实收到了声音"的门槛：低于它基本可以断定麦克风被系统
# 禁用、选错了设备，或者用户根本没出声。
_SILENCE_RMS = 0.004


def _rms_of_raw_pcm(raw: bytes) -> float:
    """算出 16-bit PCM 的平均音量（RMS）。"""
    if not raw:
        return 0.0
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _level_payload(rms: float) -> dict:
    """把 RMS 换算成前端能直接展示的百分比。0.1 记为 100%。"""
    return {
        "level": round(rms, 5),
        "level_pct": min(100, round(rms / 0.1 * 100)),
    }


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    as_file: bool = False


# ------------------------------------------------------------------
# Voice listing
# ------------------------------------------------------------------

@router.get("/voices")
async def get_voices():
    """List available TTS voices."""
    return await list_voices()


# ------------------------------------------------------------------
# Text-to-Speech
# ------------------------------------------------------------------

@router.post("/tts")
async def text_to_speech(req: TTSRequest):
    """Synthesize text to speech. Returns MP3 audio bytes."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    try:
        audio_bytes = await synthesize_to_bytes(req.text, req.voice)
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"},
        )
    except Exception as e:
        raise HTTPException(500, f"TTS synthesis failed: {str(e)}")


@router.post("/tts/file")
async def text_to_speech_file(req: TTSRequest):
    """Synthesize text to speech. Returns file path for streaming playback."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    try:
        file_path = await synthesize_to_file(req.text, req.voice)
        return FileResponse(file_path, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(500, f"TTS synthesis failed: {str(e)}")


# ------------------------------------------------------------------
# Speech-to-Text
# ------------------------------------------------------------------

@router.get("/stt-status")
async def stt_status():
    """报告本地 STT 模型的实际位置与可用性。

    打包版最常见的故障是 models/ 没打进 exe，表现却是"识别一直返回空"，
    看不出根因。这个端点把模型路径、搜索过的目录、是否 frozen 全部暴露，
    排障时一眼就能判断模型有没有被打包进去。
    """
    try:
        from ..stt import model_status
        return model_status()
    except Exception as e:
        logger.exception("STT status failed")
        raise HTTPException(500, f"STT status failed: {str(e)}")


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """Transcribe audio to text.

    Strategy:
    1. Vosk local STT (offline, fast, no API needed)
    2. HuggingFace Whisper Tiny (free, no key needed)
    3. Configured LLM endpoint's Whisper-compatible API
    """
    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "recording.webm"
        content_type = audio.content_type or "audio/webm"

        if len(audio_bytes) < 100:
            raise HTTPException(400, "Audio too short (no speech detected)")

        # --- Strategy 1: Vosk local STT (preferred - offline, fast) ---
        try:
            from ..stt import is_model_available, transcribe_wav
            if is_model_available():
                import asyncio
                text = await asyncio.to_thread(transcribe_wav, audio_bytes)
                if text:
                    logger.info("STT (Vosk local): '%s'", text[:80])
                    return {"text": text}
                else:
                    logger.info("STT (Vosk local): no speech detected")
                    return {"text": "", "info": "未检测到语音"}
        except Exception as e:
            logger.warning("Vosk STT failed: %s", e)

        # 本地模型可用时上面已经返回；能走到这里说明本地不可用。
        # 离线部署（打包版）下继续联网只会让用户每次说话都干等十几秒，
        # 因此允许用 JARVIS_STT_OFFLINE_ONLY 直接关掉网络回退。
        if os.environ.get("JARVIS_STT_OFFLINE_ONLY") == "1":
            logger.warning("STT: 本地模型不可用且已开启离线模式，跳过网络回退")
            return _stt_unavailable_response()

        # --- Strategy 2: HuggingFace free Whisper ---
        # 超时刻意压到 8s：语音链路的体验预算是 3s，这里已经是"本地模型缺失"
        # 的降级路径，再让用户等满 60s 只会让界面看起来卡死。
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api-inference.huggingface.co/models/openai/whisper-tiny",
                    headers={"Content-Type": content_type},
                    content=audio_bytes,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("text", "").strip()
                    if text:
                        logger.info("STT (HuggingFace): '%s'", text[:50])
                        return {"text": text}
        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning("HF Whisper unavailable")

        # --- Strategy 3: Configured LLM Whisper API ---
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.llm_base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    files={
                        "file": (filename, audio_bytes, content_type),
                    },
                    data={"model": "whisper-1"},
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("text", "").strip()
                    if text:
                        logger.info("STT (LLM Whisper): '%s'", text[:50])
                        return {"text": text}
        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning("LLM Whisper unavailable")

        # --- Fallback ---
        logger.warning("All STT services unavailable")
        return _stt_unavailable_response()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(500, f"STT failed: {str(e)}")


def _stt_unavailable_response() -> dict:
    """Return a graceful response when no STT service is available."""
    return {
        "text": "",
        "error": "语音识别服务暂时不可用，请稍后重试或使用文字输入",
    }


# ------------------------------------------------------------------
# Server-side recording + transcription
# Records directly from system mic using pyaudio, then transcribes.
# This bypasses browser getUserMedia AGC issues.
# ------------------------------------------------------------------

@router.post("/record/start")
async def start_recording():
    """Start recording from system microphone via pyaudio."""
    if _recording_state["active"]:
        return {"status": "already_recording"}

    try:
        _get_pyaudio()
        import pyaudio as pa

        _recording_state["frames"] = []
        _recording_state["active"] = True
        _recording_state["start_time"] = time.time()

        p = pa.PyAudio()
        stream = p.open(
            format=pa.paInt16, channels=1, rate=16000,
            input=True, frames_per_buffer=4096,
        )

        # Read in a background thread
        def _read_loop():
            import traceback
            while _recording_state["active"]:
                try:
                    data = stream.read(4096, exception_on_overflow=False)
                    _recording_state["frames"].append(data)
                except Exception:
                    logger.error("Recording read error: %s", traceback.format_exc())
                    break
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass

        threading.Thread(target=_read_loop, daemon=True).start()
        logger.info("Server-side recording started")
        return {"status": "recording"}

    except Exception as e:
        _recording_state["active"] = False
        logger.error("Failed to start recording: %s", e)
        raise HTTPException(500, f"Failed to start recording: {str(e)}")


@router.post("/record/stop")
async def stop_recording_and_transcribe():
    """Stop recording, transcribe, and return text."""
    if not _recording_state["active"]:
        raise HTTPException(400, "Not recording")

    _recording_state["active"] = False
    # Wait a moment for the recording thread to finish
    await asyncio.sleep(0.3)

    frames = _recording_state["frames"]
    duration = time.time() - _recording_state["start_time"]
    _recording_state["frames"] = []

    logger.info("Recording stopped: %.1fs, %d frames", duration, len(frames))

    if len(frames) < 2:
        return {"text": "", "duration": round(duration, 1), "error": "录音太短，请按住说完再松开"}

    raw = b"".join(frames)
    rms = _rms_of_raw_pcm(raw)

    # Encode as WAV in memory
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(raw)
    wav_bytes = buf.getvalue()

    logger.info(
        "Server recording: %.1fs, %d frames, %d bytes, rms=%.5f",
        duration, len(frames), len(wav_bytes), rms,
    )

    # 静音就直接告诉用户，不要浪费时间跑识别。
    # 麦克风被 Windows 隐私设置挡掉时 pyaudio 仍能"正常"返回全零数据，
    # 不检测音量的话用户只会看到"说了话但什么都没发生"。
    if rms < _SILENCE_RMS:
        logger.warning("Recording is silent (rms=%.5f) — mic may be muted", rms)
        return {
            "text": "",
            "duration": round(duration, 1),
            "error": "麦克风没有收到声音，请检查系统麦克风权限或默认输入设备",
            **_level_payload(rms),
        }

    # Transcribe using STT module
    try:
        from ..stt import transcribe_wav
        text = await asyncio.to_thread(transcribe_wav, wav_bytes)
        logger.info("Server recording STT result: '%s'", text[:80])
        if text.strip():
            return {"text": text, "duration": round(duration, 1), **_level_payload(rms)}
        # 有声音但没识别出内容：和"没收到声音"是两种不同的故障，必须分开说，
        # 否则用户无法判断该去查权限还是该重说一遍。
        return {
            "text": "",
            "duration": round(duration, 1),
            "error": "听到了声音但没听清，请靠近麦克风、稍微说慢一点",
            **_level_payload(rms),
        }
    except Exception as e:
        logger.error("Server recording STT error: %s", e)
        raise HTTPException(500, f"STT failed: {str(e)}")


def _record_blocking(seconds: float) -> tuple[bytes, dict | None]:
    """同步录一小段，返回 (原始 PCM, 默认输入设备信息)。

    pyaudio 的 read() 是阻塞的，必须在线程里跑，不能放进事件循环。
    """
    import pyaudio as pa

    p = pa.PyAudio()
    try:
        try:
            dev = p.get_default_input_device_info()
            device = {
                "name": dev.get("name"),
                "sample_rate": dev.get("defaultSampleRate"),
            }
        except Exception:
            device = None

        stream = p.open(
            format=pa.paInt16, channels=1, rate=16000,
            input=True, frames_per_buffer=1024,
        )
        try:
            frames = []
            for _ in range(int(16000 / 1024 * seconds)):
                frames.append(stream.read(1024, exception_on_overflow=False))
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        return b"".join(frames), device
    finally:
        try:
            p.terminate()
        except Exception:
            pass


@router.post("/mic-check")
async def mic_check(seconds: float = 2.0):
    """录一小段并返回实测音量，用于确认系统是否真的允许访问麦克风。

    "说了话没反应"最常见的三类原因在用户侧完全无法区分：麦克风被系统
    隐私设置挡掉、默认输入设备选错、本地模型没打包。这个端点直接把
    音量、设备名、模型状态一起返回，让用户在界面上就能定位是哪一类。
    """
    if _recording_state["active"]:
        raise HTTPException(409, "正在录音中，请先停止")

    seconds = max(0.5, min(seconds, 5.0))

    try:
        _get_pyaudio()
        raw, device = await asyncio.to_thread(_record_blocking, seconds)
    except RuntimeError as e:
        # pyaudio 没装：这类问题用户自己改不了，直接把安装提示透传出去
        raise HTTPException(500, str(e))
    except Exception as e:
        logger.exception("Mic check failed")
        raise HTTPException(500, f"麦克风检测失败：{e}")

    rms = _rms_of_raw_pcm(raw)
    try:
        from ..stt import model_status
        status = model_status()
        model_ready = bool(status.get("available"))
    except Exception:
        model_ready = False

    logger.info("Mic check: %.1fs, %d bytes, rms=%.5f", seconds, len(raw), rms)
    return {
        "duration": round(seconds, 1),
        "bytes": len(raw),
        "device": device,
        "heard_sound": rms >= _SILENCE_RMS,
        "model_ready": model_ready,
        **_level_payload(rms),
    }
