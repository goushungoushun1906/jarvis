"""Measure the voice pipeline latencies against a live backend.

The voice endpoints are the least measured part of the stack: Phase 17 focused
on the text/tool streaming path, and the voice budget (3s) has never been
checked. This script pins real numbers to:

* TTS time-to-first-byte and total transfer
* STT upload-to-text latency
* A synthetic end-to-end round-trip (TTS -> decode -> STT) when possible.

It boots the real backend, so the numbers include PyInstaller startup and all
runtime overheads.

Usage:
    python scripts/dev/verify_voice_latency.py [--exe]
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
import wave

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
BASE = "http://127.0.0.1:18200"


def start_backend(use_exe: bool) -> subprocess.Popen:
    if use_exe:
        cmd = [os.path.join(SERVER_DIR, "backend-dist", "backend.exe")]
        cwd = os.path.join(SERVER_DIR, "backend-dist")
    else:
        cmd = [os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe"),
               "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
               "--port", "18200"]
        cwd = SERVER_DIR
    return subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def wait_ready(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(f"{BASE}/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def make_test_wav(path: str, duration: float = 2.0, freq: float = 440.0) -> None:
    """Generate a 16kHz mono sine WAV for STT latency testing.

    It is not real speech, so the transcription will likely be empty — the
    point is to measure how long the endpoint takes to respond.
    """
    rate = 16000
    samples = int(rate * duration)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        data = bytearray()
        for i in range(samples):
            v = int(32767 * 0.3 * (1 + 0.5 * (i / samples)) *
                    (1 + 0.5 * (1 if i % (rate // 5) < 400 else -1)))
            data.extend(struct.pack("<h", v))
        w.writeframes(data)


def ttfb_post(path: str, **kwargs) -> tuple[int, float, float, bytes]:
    """Return (status, ttfb_ms, total_ms, body) for a POST."""
    started = time.perf_counter()
    first_byte: float | None = None
    chunks: list[bytes] = []
    with httpx.Client(timeout=180, trust_env=False) as c:
        with c.stream("POST", f"{BASE}{path}", **kwargs) as resp:
            for chunk in resp.iter_bytes(4096):
                if first_byte is None:
                    first_byte = time.perf_counter()
                chunks.append(chunk)
    total = time.perf_counter()
    ttfb = (first_byte - started) * 1000 if first_byte else (total - started) * 1000
    return (resp.status_code, ttfb, (total - started) * 1000, b"".join(chunks))


def check_tts(text: str = "你好，贾维斯") -> list[tuple[str, bool, str]]:
    print(f"\n--- TTS: {text!r} ---", flush=True)
    rows = []
    status, ttfb, total, body = ttfb_post("/api/voice/tts", json={"text": text})
    ok = status == 200 and len(body) > 1000
    rows.append(("TTS 请求成功", ok,
                 f"status={status}, body={len(body)} bytes"))
    rows.append(("TTS 首字节时间", ok, f"+{ttfb:.0f} ms"))
    rows.append(("TTS 总耗时", ok, f"+{total:.0f} ms"))
    return rows


def check_stt(wav_path: str) -> list[tuple[str, bool, str]]:
    print("\n--- STT ---", flush=True)
    rows = []
    started = time.perf_counter()
    with httpx.Client(timeout=60, trust_env=False) as c:
        r = c.post(f"{BASE}/api/voice/stt",
                   files={"audio": ("latency_test.wav", open(wav_path, "rb"),
                                    "audio/wav")})
    total = (time.perf_counter() - started) * 1000
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    text = body.get("text", "")
    rows.append(("STT 请求成功", r.status_code == 200,
                 f"status={r.status_code}"))
    rows.append(("STT 总耗时", r.status_code == 200, f"+{total:.0f} ms"))
    rows.append(("STT 识别结果", True,
                 f"{text!r}" + (f" (err={body.get('error')})" if body.get("error") else "")))
    return rows


def check_tts_to_stt() -> list[tuple[str, bool, str]]:
    print("\n--- TTS -> STT 闭环（如 TTS 不可用则跳过）---", flush=True)
    rows = []
    status, ttfb, total, mp3 = ttfb_post("/api/voice/tts", json={"text": "测试"})
    if status != 200 or len(mp3) < 1000:
        rows.append(("TTS 未返回音频，跳过闭环", True, f"status={status} bytes={len(mp3)}"))
        return rows

    # Try to decode MP3 and feed it back. Use a lightweight pure-Python MP3
    # decoder if available; otherwise save the MP3 and let the user decode it.
    try:
        import io
        from pydub import AudioSegment
        seg = AudioSegment.from_mp3(io.BytesIO(mp3))
        wav = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2).raw_data
        with wave.open(os.path.join(os.environ.get("TEMP", "/tmp"), "tts_loopback.wav"), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(wav)
        wav_path = os.path.join(os.environ.get("TEMP", "/tmp"), "tts_loopback.wav")
        started = time.perf_counter()
        with httpx.Client(timeout=60, trust_env=False) as c:
            r = c.post(f"{BASE}/api/voice/stt",
                       files={"audio": ("loopback.wav", open(wav_path, "rb"), "audio/wav")})
        stt_total = (time.perf_counter() - started) * 1000
        try:
            body = r.json()
        except Exception:
            body = {}
        rows.append(("TTS->STT 闭环请求成功", r.status_code == 200,
                     f"status={r.status_code}, text={body.get('text', '')!r}"))
        rows.append(("STT 在闭环中耗时", r.status_code == 200, f"+{stt_total:.0f} ms"))
    except ImportError:
        rows.append(("TTS->STT 闭环", False,
                     "缺少 pydub，无法把 TTS 的 MP3 转回 WAV 给 STT"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", action="store_true", help="使用打包后的 backend.exe")
    args = ap.parse_args()

    wav_path = os.path.join(os.environ.get("TEMP", "/tmp"), "jarvis_voice_test.wav")
    make_test_wav(wav_path)
    print(f"测试 WAV 已生成: {wav_path}", flush=True)

    proc = start_backend(args.exe)
    try:
        if not wait_ready(120):
            print("后端未就绪，中止", flush=True)
            return 1

        rows: list[tuple[str, bool, str]] = []
        rows += check_tts()
        rows += check_stt(wav_path)
        rows += check_tts_to_stt()

        print("\n=== 语音延迟报告 ===", flush=True)
        failed = 0
        for label, ok, detail in rows:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"[{mark}] {label}  ({detail})", flush=True)
        print(f"\n结果: {len(rows)} 项, {'全部通过' if failed == 0 else f'{failed} 项失败'}",
              flush=True)
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
