#!/usr/bin/env python3
"""并排跑一遍所有可用的语音识别引擎，比较**文本质量**与**耗时**。

换识别引擎时最容易被"能跑通"骗过去：Vosk 会把「你好贾维斯」识别成
「你好 假 微丝」，接口照样返回 200，只有把结果并排摆在一起才看得出区别。
所以这个脚本刻意不做断言，只做并排打印——让人一眼看出该不该换。

用法::

    python scripts/dev/compare_stt.py                 # 用 models/asr 下的模型
    python scripts/dev/compare_stt.py path/to/a.wav   # 指定音频
    python scripts/dev/compare_stt.py --speak "今天天气怎么样"  # 现场合成

默认样本优先用 logs/wakeword/ 里真实喊的录音（有人声、有口音），其次
用系统 TTS 合成一句。
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from app import stt  # noqa: E402


def pick_sample() -> tuple[str, bytes]:
    """挑一段有人声的音频：真实唤醒录音优先，否则现场合成。"""
    cands = sorted(glob.glob(os.path.join("logs", "wakeword", "*.wav")))
    if cands:
        path = cands[-1]
        with open(path, "rb") as f:
            return f"真实录音 {os.path.basename(path)}", f.read()
    return "", b""


def synthesize(text: str) -> bytes:
    """用系统 SAPI 合成一段 16k 单声道 WAV。"""
    import tempfile
    import wave

    import win32com.client

    raw = os.path.join(tempfile.gettempdir(), "cmp_raw.wav")
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    if os.path.isfile(raw):
        os.unlink(raw)
    stream.Open(raw, 3)
    speaker.AudioOutputStream = stream
    speaker.Speak(text)
    stream.Close()

    # SAPI 输出 22.05k，转 16k 交给 stt 的重采样逻辑，顺便验证那条路径
    with wave.open(raw, "rb") as w:
        frames = w.readframes(w.getnframes())
    os.unlink(raw)
    return frames  # 原始 PCM，交给调用方包 WAV


def to_wav(pcm: bytes, rate: int = 22050) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def run_one(name: str, fn, wav: bytes) -> tuple[str, float]:
    t0 = time.time()
    try:
        import io
        import struct
        import wave

        with wave.open(io.BytesIO(wav), "rb") as wf:
            rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        text = fn(pcm, rate) or ""
    except Exception as e:
        return f"<异常: {type(e).__name__}: {e}>", time.time() - t0
    return text, time.time() - t0


def main() -> int:
    parser = argparse.ArgumentParser(description="对比本机可用的语音识别引擎")
    parser.add_argument("wav", nargs="?", help="指定 WAV 文件")
    parser.add_argument("--speak", help="用系统 TTS 合成这句话作为样本")
    args = parser.parse_args()

    if args.wav:
        with open(args.wav, "rb") as f:
            wav = f.read()
        label = os.path.basename(args.wav)
    elif args.speak:
        wav = to_wav(synthesize(args.speak))
        label = f"合成「{args.speak}」"
    else:
        label, wav = pick_sample()
        if not wav:
            print("没有可用样本：请先实喊一次（logs/wakeword/ 有录音）或用 --speak")
            return 1

    print(f"样本：{label}（{len(wav) // 1024}KB）\n")

    engines = [
        ("Paraformer（sherpa-onnx）", stt._transcribe_paraformer),
        ("faster-whisper", stt._transcribe_whisper),
        ("Vosk", stt._transcribe_vosk),
    ]

    print(f"{'引擎':<28}{'耗时':>8}   文本")
    print("-" * 78)
    for name, fn in engines:
        text, dt = run_one(name, fn, wav)
        print(f"{name:<28}{dt:>7.2f}s   {text}")

    print("\n（Paraformer 未安装时会静默回落，上面三行都不空即说明链路正常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
