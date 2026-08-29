"""Voice/TTS/STT API routes."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave

import httpx
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
    """Lazily import and return pyaudio (avoids startup cost)."""
    import sys, os
    # Find pyaudio_pkg: search upward from this file until we find jarvis/pyaudio_pkg
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):  # max depth search
        candidate = os.path.join(d, "pyaudio_pkg")
        if os.path.isdir(candidate):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            break
        d = os.path.dirname(d)
    import pyaudio
    return pyaudio


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

        # --- Strategy 2: HuggingFace free Whisper ---
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
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
        return {"text": "", "info": "录音太短"}

    # Encode as WAV in memory
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        for f in frames:
            wf.writeframes(f)
    wav_bytes = buf.getvalue()

    logger.info(
        "Server recording: %.1fs, %d frames, %d bytes",
        duration, len(frames), len(wav_bytes),
    )

    # Transcribe using STT module
    try:
        from ..stt import transcribe_wav
        text = await asyncio.to_thread(transcribe_wav, wav_bytes)
        logger.info("Server recording STT result: '%s'", text[:80])
        return {"text": text, "duration": round(duration, 1)}
    except Exception as e:
        logger.error("Server recording STT error: %s", e)
        raise HTTPException(500, f"STT failed: {str(e)}")
