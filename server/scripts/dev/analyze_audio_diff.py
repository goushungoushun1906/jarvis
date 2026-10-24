"""对比「能唤醒的历史捕获」与「唤不醒的实时录音」的声学特征。

目的：判断实时链路是否被驱动做了 AGC（自动增益）/ 降噪 / 波束成形处理。
判据：
  - 动态范围（语音段能量 / 静音段能量）：正常麦克风应 > 20dB；
    开了 AGC 的阵列麦会把静音段也抬起来，动态范围往往 < 10dB。
  - 静音段底噪：AGC 会把底噪抬到接近语音电平。
"""

from __future__ import annotations

import sys
import wave
import array
import math
from pathlib import Path


def load(path: str) -> tuple[array.array, int]:
    with wave.open(path, "rb") as f:
        rate = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return a, rate


def db(x: float) -> float:
    return 20 * math.log10(max(1e-9, x))


def frame_rms(a: array.array, win: int = 320) -> list[float]:
    out = []
    for i in range(0, len(a) - win, win):
        seg = a[i : i + win]
        out.append((sum((x / 32768.0) ** 2 for x in seg) / win) ** 0.5)
    return out


def analyze(path: str) -> dict:
    a, rate = load(path)
    if len(a) < 1600:
        return {"path": path, "error": "too short"}

    frames = frame_rms(a)
    frames_sorted = sorted(frames)
    n = len(frames_sorted)

    # 静音段：能量最低的 25% 帧；语音段：能量最高的 25% 帧
    lo = frames_sorted[: max(1, n // 4)]
    hi = frames_sorted[-max(1, n // 4) :]
    floor = sum(lo) / len(lo)
    speech = sum(hi) / len(hi)

    peak = max(abs(x) for x in a) / 32768.0
    overall = (sum((x / 32768.0) ** 2 for x in a) / len(a)) ** 0.5

    # 动态范围
    dyn = db(speech) - db(floor) if floor > 0 else float("inf")

    return {
        "path": Path(path).name,
        "rate": rate,
        "dur_s": len(a) / rate,
        "overall_rms": round(overall, 5),
        "peak": round(peak, 4),
        "noise_floor_rms": round(floor, 5),
        "speech_rms": round(speech, 5),
        "dynamic_range_db": round(dyn, 1),
        "frames": n,
    }


def main() -> int:
    groups = {
        "【历史捕获 · KWS 能命中】": [
            "logs/wakeword/20261014-070014-你好贾维斯-100.wav",
            "logs/wakeword/20261014-203050-你好贾维斯-100.wav",
            "logs/wakeword/20261014-203116-你好贾维斯-100.wav",
        ],
        "【实时录音 · KWS 唤不醒】": [
            "logs/wakeword-live/03_rms0.0089_peak0.081.wav",
            "logs/wakeword-live/44_rms0.0079_peak0.072.wav",
            "logs/wakeword-live/32_rms0.0054_peak0.047.wav",
            "logs/wakeword-live/06_rms0.0112_peak0.142.wav",
        ],
    }

    for title, paths in groups.items():
        print(title)
        print(
            f"  {'文件':<40s} {'时长':>6s} {'峰值':>7s} {'底噪rms':>9s} {'语音rms':>9s} {'动态范围':>9s}"
        )
        for p in paths:
            fp = Path(p)
            if not fp.is_file():
                print(f"  {p:<40s} (文件不存在)")
                continue
            r = analyze(p)
            if "error" in r:
                print(f"  {r['path']:<40s} {r['error']}")
                continue
            print(
                f"  {r['path']:<40s} {r['dur_s']:>5.2f}s {r['peak']:>7.3f} "
                f"{r['noise_floor_rms']:>9.5f} {r['speech_rms']:>9.5f} {r['dynamic_range_db']:>8.1f}dB"
            )
        print()

    print("判读：动态范围 < 12dB 且底噪接近语音电平 → 驱动开了 AGC/降噪，把信号压平了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
