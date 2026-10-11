"""Verify offline STT works in the packaged backend.

Why this exists
---------------
The packaged build used to answer every STT request with an empty string after
~14 seconds. The cause was invisible from the outside: ``models/`` was never
added to the PyInstaller ``datas``, so the frozen build could not find the Vosk
model, fell through to the network Whisper fallback, and timed out. The symptom
("recognition returns nothing") looked nothing like the cause ("model not
packaged").

This script pins the whole chain down against the real ``backend.exe``:

1. boots the packaged backend (or dev uvicorn with ``--dev``)
2. reports ``/api/voice/stt-status`` — model path, search roots, frozen flag
3. transcribes a real Chinese speech sample and asserts text comes back
4. checks the latency against the 3s voice budget

Speech sample
-------------
Real speech is required to prove recognition, not just latency. If ``--wav`` is
not given, one is synthesised with edge_tts and converted to 16kHz mono WAV
with ffmpeg (both must be on PATH). Without them it degrades to a sine tone,
which still measures latency but cannot prove accuracy.

Usage:
    python scripts/dev/verify_stt_packaged.py [--exe PATH] [--dev] [--wav PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_EXE = os.path.join(SERVER_DIR, "backend-dist", "backend.exe")
BASE = "http://127.0.0.1:18200"

# 语音链路预算：开发方案里给语音交互定的是 3s。
LATENCY_BUDGET_S = 3.0

SAMPLE_TEXT = "你好贾维斯，现在几点了"


def resolve_port() -> int:
    """用独立端口，避免撞上正在运行的 JARVIS（默认 18200）。"""
    return int(os.environ.get("JARVIS_VERIFY_PORT", "18233"))


def start_backend(use_dev: bool, exe_path: str) -> subprocess.Popen:
    if use_dev:
        venv_py = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
        if not os.path.exists(venv_py):  # pragma: no cover
            venv_py = sys.executable
        cmd = [venv_py, "-m", "uvicorn", "main:app",
               "--host", "127.0.0.1", "--port", str(resolve_port())]
        cwd = SERVER_DIR
    else:
        cmd = [exe_path]
        cwd = os.path.dirname(exe_path) or "."
    env = {**os.environ, "SERVER_PORT": str(resolve_port())}
    if not use_dev:
        # 打包版默认要求 per-request token；本脚本只关心语音链路，显式关掉。
        env["JARVIS_DISABLE_API_TOKEN"] = "1"
    return subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(base: str, timeout: int = 240) -> bool:
    """打包版要先把 ~300MB 解压到 _MEIPASS，启动比 dev 慢得多。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(f"{base}/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def make_speech_sample(dest_dir: str) -> tuple[str, str] | None:
    """用 edge_tts + ffmpeg 合成一段真实中文语音。

    返回 (wav_path, 说明)；缺少依赖时返回 None。
    """
    if not shutil.which("ffmpeg"):
        return None
    mp3 = os.path.join(dest_dir, "verify_stt_sample.mp3")
    wav = os.path.join(dest_dir, "verify_stt_sample.wav")
    try:
        import asyncio

        import edge_tts

        async def _synth() -> None:
            comm = edge_tts.Communicate(SAMPLE_TEXT, "zh-CN-XiaoxiaoNeural")
            await comm.save(mp3)

        asyncio.run(_synth())
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3, "-ar", "16000", "-ac", "1",
             "-sample_fmt", "s16", wav],
            check=True, capture_output=True,
        )
        return wav, f"合成语音：{SAMPLE_TEXT!r}"
    except Exception:
        return None


def make_tone_sample(dest_dir: str) -> tuple[str, str]:
    """退而求其次：正弦音只能测延迟，证明不了识别准确率。"""
    import struct
    import wave

    wav = os.path.join(dest_dir, "verify_stt_tone.wav")
    rate, seconds = 16000, 2.0
    with wave.open(wav, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            frames.extend(struct.pack("<h", int(32767 * 0.3 * (i % 200) / 200)))
        w.writeframes(bytes(frames))
    return wav, "正弦音（无真实语音，只能测延迟）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE, help="打包后的 backend.exe 路径")
    ap.add_argument("--dev", action="store_true", help="改用 dev uvicorn 做对照")
    ap.add_argument("--wav", help="指定用于识别的 16kHz mono WAV")
    args = ap.parse_args()

    if not args.dev and not os.path.exists(args.exe):
        print(f"[FAIL] 找不到 {args.exe}")
        return 1

    tmp_dir = os.environ.get("TEMP", "/tmp")
    if args.wav:
        wav_path, sample_desc = args.wav, f"指定样本 {args.wav}"
        real_speech = True
    else:
        made = make_speech_sample(tmp_dir)
        if made:
            wav_path, sample_desc = made
            real_speech = True
        else:
            wav_path, sample_desc = make_tone_sample(tmp_dir)
            real_speech = False

    port = resolve_port()
    base = f"http://127.0.0.1:{port}"
    print(f"后端: {'dev uvicorn' if args.dev else args.exe}")
    print(f"端口: {port}")
    print(f"样本: {sample_desc}\n")

    proc = start_backend(args.dev, args.exe)
    rows: list[tuple[str, bool, str]] = []
    try:
        if not wait_ready(base):
            print("[FAIL] 后端在 240s 内未就绪")
            return 1

        with httpx.Client(timeout=60, trust_env=False) as c:
            status = c.get(f"{base}/api/voice/stt-status").json()
        print("模型状态:", json.dumps(status, ensure_ascii=False), "\n")

        rows.append(("本地模型可用", bool(status.get("available")),
                     f"available={status.get('available')}"))
        model_path = status.get("vosk_model")
        rows.append(("已定位到模型目录", bool(model_path),
                     model_path or "未找到"))
        if args.dev:
            rows.append(("运行在 frozen 模式", False, "dev 模式（此项不适用）"))
        else:
            rows.append(("运行在 frozen 模式", bool(status.get("frozen")),
                         f"frozen={status.get('frozen')}"))

        with open(wav_path, "rb") as f:
            wav_bytes = f.read()

        started = time.perf_counter()
        with httpx.Client(timeout=120, trust_env=False) as c:
            resp = c.post(f"{base}/api/voice/stt",
                          files={"audio": ("speech.wav", wav_bytes, "audio/wav")})
        elapsed = time.perf_counter() - started

        rows.append(("STT 请求成功", resp.status_code == 200,
                     f"status={resp.status_code}"))
        try:
            body = resp.json()
        except Exception:
            body = {}
        text = (body.get("text") or "").strip()
        rows.append(("STT 耗时在预算内", elapsed <= LATENCY_BUDGET_S,
                     f"{elapsed:.2f}s（预算 {LATENCY_BUDGET_S:.0f}s）"))

        if real_speech:
            rows.append(("识别出非空文本", bool(text), repr(text)))
        else:
            rows.append(("STT 返回", True,
                         f"{text!r}（正弦音样本，不校验准确率）"))

        print("\n=== 打包版离线 STT 报告 ===")
        failed = 0
        for label, ok, detail in rows:
            if not ok:
                failed += 1
            print(f"[{'PASS' if ok else 'FAIL'}] {label}  ({detail})")
        print(f"\n结果: {len(rows)} 项，{'全部通过' if failed == 0 else f'{failed} 项失败'}")
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
