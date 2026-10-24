"""验证「频谱凹陷是否随输入电平变化」。

假设：英特尔阵列麦的驱动在**低输入电平**下启用更强的降噪/AGC，
      把 500-2000Hz（元音共振峰区）的能量压掉。这能解释两个反常现象：
        1. 实时录音中频比历史捕获低 55%
        2. 软件放大无效——放大发生在驱动处理**之后**，补不回被削掉的频段

做法：把同一段历史捕获按不同音量从扬声器播出，用同一条通道录制。
      如果小音量回放也出现中频凹陷，就证实是电平依赖的驱动处理。
"""

from __future__ import annotations

import array
import os
import sys
import wave

os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np  # noqa: E402
import pyaudio  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REF = os.path.join(BASE, "logs", "wakeword", "20261014-203116-你好贾维斯-100.wav")
OUT = os.path.join(BASE, "logs", "wakeword-level")
RATE = 16000
CHUNK = 1024
DEV = 5  # 当前生产在用的通道

BANDS = [(0, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000)]


def load(path: str) -> np.ndarray:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def save(a: np.ndarray, path: str) -> None:
    b = np.clip(a * 32767, -32767, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b.tobytes())


def play_and_record(pa, dev: int, sig: np.ndarray) -> np.ndarray:
    pcm = np.clip(sig * 32767, -32767, 32767).astype("<i2")
    lead = np.zeros(int(RATE * 0.5), dtype="<i2")
    tail = np.zeros(int(RATE * 0.8), dtype="<i2")
    full = np.concatenate([lead, pcm, tail])

    out_s = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    output=True, frames_per_buffer=CHUNK)
    in_s = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                   input_device_index=dev, frames_per_buffer=CHUNK)
    rec = []
    try:
        for i in range(0, len(full), CHUNK):
            c = full[i : i + CHUNK]
            if len(c) < CHUNK:
                c = np.concatenate([c, np.zeros(CHUNK - len(c), dtype="<i2")])
            out_s.write(c.tobytes())
            try:
                rec.append(np.frombuffer(in_s.read(CHUNK, exception_on_overflow=False),
                                         dtype="<i2"))
            except Exception:
                rec.append(np.zeros(CHUNK, dtype="<i2"))
        for _ in range(int(RATE * 0.3 / CHUNK) + 1):
            try:
                rec.append(np.frombuffer(in_s.read(CHUNK, exception_on_overflow=False),
                                         dtype="<i2"))
            except Exception:
                break
    finally:
        for s in (out_s, in_s):
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
    return np.concatenate(rec).astype(np.float32) / 32768.0


def ltas(a: np.ndarray) -> list[float]:
    win, hop = 400, 160
    specs = []
    for i in range(0, len(a) - win, hop):
        seg = a[i : i + win]
        if (seg**2).mean() < 1e-6:
            continue
        specs.append(np.abs(np.fft.rfft(seg * np.hanning(win))))
    if not specs:
        return [0.0] * len(BANDS)
    S = np.mean(specs, axis=0)
    f = np.fft.rfftfreq(win, 1 / RATE)
    S = S / np.max(S)
    return [float(S[(f >= lo) & (f < hi)].mean()) for lo, hi in BANDS]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    ref = load(REF)

    scales = [("响 (峰值0.95)", 0.95 / float(np.max(np.abs(ref)))),
              ("中 (峰值0.35)", 0.35 / float(np.max(np.abs(ref)))),
              ("轻 (峰值0.12)", 0.12 / float(np.max(np.abs(ref))))]

    pa = pyaudio.PyAudio()
    print(f"回放源：{os.path.basename(REF)}   录制通道：dev {DEV}\n")
    print(f"{'音量档':<16s} {'录音峰值':>9s} " + " ".join(f"{lo}-{hi}"[:9].rjust(9) for lo, hi in BANDS))
    print("-" * 76)

    results = {}
    for label, g in scales:
        rec = play_and_record(pa, DEV, ref * g)
        peak = float(np.max(np.abs(rec)))
        bands = ltas(rec)
        name = f"lv{label.split()[0]}_peak{peak:.3f}.wav"
        save(rec, os.path.join(OUT, name))
        results[label] = bands
        print(f"{label:<16s} {peak:>9.3f} " + " ".join(f"{b:>9.3f}" for b in bands))

    pa.terminate()

    print("\n对比基准：")
    print(f"{'历史原始':<16s} {'':>9s} " +
          " ".join(f"{b:>9.3f}" for b in ltas(ref)))
    print(f"{'实时06':<16s} {'':>9s} " +
          " ".join(f"{b:>9.3f}" for b in ltas(load(
              os.path.join(BASE, "logs", "wakeword-live",
                           "06_rms0.0112_peak0.142.wav")))))

    loud = results["响 (峰值0.95)"]
    soft = results["轻 (峰值0.12)"]
    mid_band = 2  # 500-1000Hz
    print(f"\n中频(500-1000Hz)：响={loud[mid_band]:.3f}  轻={soft[mid_band]:.3f}  "
          f"变化 {(soft[mid_band]/max(1e-9,loud[mid_band])-1)*100:+.0f}%")
    print("若「轻」档中频明显低于「响」档 → 证实驱动在低电平下压中频，"
          "需提高 Windows 麦克风输入音量。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
