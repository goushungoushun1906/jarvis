"""JARVIS Wake Word Detection — continuous background listening.

Uses Porcupine (pvporcupine) with built-in "jarvis" keyword for fast,
accurate, offline wake word detection.

Requires a free Picovoice AccessKey (sign up at https://console.picovoice.ai/).
The AccessKey is stored in the .env file as PORCUPINE_ACCESS_KEY.

If no AccessKey is configured, falls back to Google Web Speech API via
speech_recognition library (requires internet).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("jarvis.wakeword")


class WakeWordStatus(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    DETECTED = "detected"
    ERROR = "error"


@dataclass
class DetectionEvent:
    keyword: str
    confidence: float
    transcript: str  # empty for porcupine, filled for speech API
    timestamp: float


# Phrases detected by the fallback speech recognition
WAKE_PHRASES = [
    "hey jarvis", "jarvis", "贾维斯", "你好贾维斯",
    "hey g a r v i s", "j a r v i s", "贾维",
]


def is_wake_phrase(text: str) -> tuple[bool, str, float]:
    if not text:
        return False, "", 0.0
    lower = text.lower().strip()
    for phrase in WAKE_PHRASES:
        if phrase in lower:
            ratio = len(phrase) / max(len(lower), 1)
            return True, phrase, round(min(0.5 + ratio * 0.5, 1.0), 3)
    return False, "", 0.0


class WakeWordService:
    """Manages the wake word background listener."""

    def __init__(self, access_key: str = ""):
        self._access_key = access_key
        self._status = WakeWordStatus.IDLE
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._detection_event = asyncio.Event()
        self._recent_detections: list[DetectionEvent] = []
        self._lock = threading.Lock()
        self._cooldown = 4.0
        self._last_detection_time = 0.0
        self._method = "none"

    @property
    def status(self) -> WakeWordStatus:
        return self._status

    @property
    def model_names(self) -> list[str]:
        return ["porcupine"] if self._method == "porcupine" else ["google_speech_api"]

    @property
    def peak_confidence(self) -> float:
        return 0.0

    async def start(self) -> dict:
        if self._status == WakeWordStatus.LISTENING:
            return {"status": "already_listening", "method": self._method}

        # Try Porcupine first (fast, offline, accurate)
        if self._access_key:
            try:
                return self._start_porcupine()
            except Exception as e:
                logger.warning("Porcupine failed, falling back to speech API: %s", e)

        # Fallback: Google Speech API
        try:
            import speech_recognition  # noqa: F401
            return self._start_speech_api()
        except ImportError:
            pass

        # Last resort: simple energy-based VAD with pyaudio
        try:
            import pyaudio  # noqa: F401
            return self._start_energy_only()
        except ImportError:
            self._status = WakeWordStatus.ERROR
            raise RuntimeError(
                "No wake word backend available. "
                "Install one of: "
                "1) pip install pvporcupine + set PORCUPINE_ACCESS_KEY in .env "
                "2) pip install SpeechRecognition "
                "3) pip install pyaudio (for energy-only mode)"
            )

    def _start_porcupine(self) -> dict:
        import pvporcupine

        self._porcupine = pvporcupine.create(
            keywords=["jarvis"],
            access_key=self._access_key,
        )
        self._method = "porcupine"

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_porcupine, daemon=True, name="wakeword-porcupine"
        )
        self._thread.start()
        self._status = WakeWordStatus.LISTENING
        logger.info("Wake word listener started (method: porcupine, keyword: jarvis)")
        return {"status": "listening", "method": "porcupine", "keyword": "jarvis"}

    def _listen_porcupine(self) -> None:
        import pyaudio as pa

        pa2 = pa.PyAudio()
        stream = pa2.open(
            format=pa.paInt16,
            channels=1,
            rate=self._porcupine.sample_rate,
            input=True,
            frames_per_buffer=self._porcupine.frame_length,
        )
        logger.info("Porcupine mic stream opened")

        try:
            while not self._stop_event.is_set():
                try:
                    frame = stream.read(self._porcupine.frame_length, exception_on_overflow=False)
                except Exception:
                    continue

                result = self._porcupine.process(frame)
                if result:
                    now = time.time()
                    if now - self._last_detection_time < self._cooldown:
                        continue
                    self._last_detection_time = now

                    event = DetectionEvent(
                        keyword="jarvis",
                        confidence=1.0,
                        transcript="",
                        timestamp=now,
                    )
                    logger.info("WAKE WORD DETECTED: jarvis (porcupine)")

                    with self._lock:
                        self._recent_detections.append(event)
                        self._recent_detections = self._recent_detections[-10:]

                    self._status = WakeWordStatus.DETECTED
                    self._detection_event.set()
                    time.sleep(0.5)
                    self._status = WakeWordStatus.LISTENING
        finally:
            stream.stop_stream()
            stream.close()
            pa2.terminate()
            self._porcupine.delete()

    def _start_speech_api(self) -> dict:
        self._method = "google_speech_api"

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_speech_api, daemon=True, name="wakeword-speech"
        )
        self._thread.start()
        self._status = WakeWordStatus.LISTENING
        logger.info("Wake word listener started (method: google_speech_api)")
        return {"status": "listening", "method": "google_speech_api", "phrases": WAKE_PHRASES}

    def _listen_speech_api(self) -> None:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.6
        recognizer.phrase_threshold = 0.3

        mic = sr.Microphone(sample_rate=16000)

        logger.info("Calibrating for ambient noise...")
        try:
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as e:
            logger.warning("Noise calibration failed: %s", e)

        while not self._stop_event.is_set():
            try:
                with mic as source:
                    audio = recognizer.listen(source, timeout=2, phrase_time_limit=5)
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                logger.warning("Mic error: %s", e)
                time.sleep(0.5)
                continue

            text = ""
            for lang in ("en-US", "zh-CN"):
                try:
                    text = recognizer.recognize_google(audio, language=lang)
                    if text:
                        break
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as e:
                    logger.warning("API error (%s): %s", lang, e)
                    time.sleep(2)
                    break
                except Exception:
                    continue

            if not text:
                continue

            detected, phrase, confidence = is_wake_phrase(text)
            if detected:
                now = time.time()
                if now - self._last_detection_time < self._cooldown:
                    continue
                self._last_detection_time = now

                event = DetectionEvent(
                    keyword=phrase,
                    confidence=confidence,
                    transcript=text,
                    timestamp=now,
                )
                logger.info("WAKE WORD DETECTED: '%s' (heard: '%s')", phrase, text)

                with self._lock:
                    self._recent_detections.append(event)
                    self._recent_detections = self._recent_detections[-10:]

                self._status = WakeWordStatus.DETECTED
                self._detection_event.set()
                time.sleep(0.5)
                self._status = WakeWordStatus.LISTENING
            else:
                logger.debug("Heard (no wake): '%s'", text)

    def _start_energy_only(self) -> dict:
        """Energy-based VAD only — detects when someone is speaking,
        then shows 'detected' to encourage saying 'Jarvis'."""
        self._method = "energy_vad"

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_energy, daemon=True, name="wakeword-energy"
        )
        self._thread.start()
        self._status = WakeWordStatus.LISTENING
        logger.info("Wake word listener started (method: energy_vad — demo mode)")
        return {"status": "listening", "method": "energy_vad"}

    def _listen_energy(self) -> None:
        import pyaudio as pa
        import array

        pa2 = pa.PyAudio()
        stream = pa2.open(
            format=pa.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=512,
        )
        logger.info("Energy VAD mic stream opened")

        threshold = 1500
        speech_frames = 0
        silence_frames = 0

        try:
            while not self._stop_event.is_set():
                try:
                    data = stream.read(512, exception_on_overflow=False)
                except Exception:
                    continue

                # Calculate RMS energy
                samples = array.array('h', data)
                rms = sum(s * s for s in samples) / len(samples) ** 0.5

                if rms > threshold:
                    speech_frames += 1
                    silence_frames = 0
                else:
                    silence_frames += 1
                    if silence_frames > 30 and speech_frames > 10:
                        # Speech burst detected
                        now = time.time()
                        if now - self._last_detection_time >= self._cooldown:
                            self._last_detection_time = now
                            event = DetectionEvent(
                                keyword="(voice activity)",
                                confidence=0.3,
                                transcript="(energy detected, say 'Jarvis' to activate)",
                                timestamp=now,
                            )
                            with self._lock:
                                self._recent_detections.append(event)
                                self._recent_detections = self._recent_detections[-10:]
                            self._status = WakeWordStatus.DETECTED
                            self._detection_event.set()
                            time.sleep(0.5)
                            self._status = WakeWordStatus.LISTENING
                        speech_frames = 0
                        silence_frames = 0
        finally:
            stream.stop_stream()
            stream.close()
            pa2.terminate()

    async def stop(self) -> dict:
        if self._status == WakeWordStatus.IDLE:
            return {"status": "already_idle"}
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._status = WakeWordStatus.IDLE
        self._detection_event.clear()
        logger.info("Wake word listener stopped")
        return {"status": "idle", "method": self._method}

    async def wait_for_detection(self, timeout: float = 3600) -> Optional[DetectionEvent]:
        self._detection_event.clear()
        try:
            await asyncio.wait_for(self._detection_event.wait(), timeout=timeout)
            with self._lock:
                return self._recent_detections[-1] if self._recent_detections else None
        except asyncio.TimeoutError:
            return None

    def get_recent_detections(self) -> list[dict]:
        with self._lock:
            events = [
                {
                    "keyword": e.keyword,
                    "confidence": e.confidence,
                    "transcript": e.transcript,
                    "timestamp": e.timestamp,
                }
                for e in self._recent_detections
            ]
            self._recent_detections.clear()
        return events

    def get_info(self) -> dict:
        return {
            "status": self._status.value,
            "models": self.model_names,
            "method": self._method,
            "threshold": 0.5,
            "peak_confidence": 0.0,
            "cooldown": self._cooldown,
            "wake_phrases": WAKE_PHRASES,
        }


# Load access key from environment
_access_key = os.environ.get("PORCUPINE_ACCESS_KEY", "")

# Global singleton
wake_word_service = WakeWordService(access_key=_access_key)
