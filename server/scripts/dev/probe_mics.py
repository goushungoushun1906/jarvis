"""麦克风体检：逐条打开输入通道，逐帧统计，看谁真的在收音。

用法：
    .venv/Scripts/python.exe scripts/dev/probe_mics.py

为什么要这个脚本
----------------
"喊了没反应"在用户侧有三种完全无法区分的原因：系统隐私设置把麦克风挡了、
默认输入设备选错了、模型没打包。前两种表现一模一样：程序一切正常、日志全
绿，但送进模型的从头到尾都是静音。

只测"安静时的底噪"来选设备不够稳——环境噪声本身会波动，同一条通道不同时
刻能差好几倍（本机默认通道实测在 0.0008 ~ 0.006 之间跳）。所以这里除了
平均值，还看**逐帧的离散程度**：真麦克风的环境噪声每帧都在随机起伏，而
死通道/坏驱动要么恒为 0，要么返回一个固定的垃圾模式（读数一帧不差）。
"""

from __future__ import annotations

import math
import struct
import sys
import time

RATE_TRIES = (16000, 44100, 48000)
PROBE_SECONDS = 1.5
CHUNK = 1024


def _rms16(data: bytes) -> float:
    n = len(data) // 2
    if n <= 0:
        return 0.0
    samples = struct.unpack_from(f"<{n}h", data)
    total = 0.0
    for s in samples:
        v = s / 32768.0
        total += v * v
    return math.sqrt(total / len(samples))


def probe(index: int, rate: int) -> dict[str, float] | None:
    import pyaudio as pa

    p = pa.PyAudio()
    try:
        try:
            stream = p.open(
                format=pa.paInt16,
                channels=1,
                rate=rate,
                input=True,
                input_device_index=index,
                frames_per_buffer=CHUNK,
            )
        except Exception:
            return None
        try:
            # 预热：丢掉设备刚打开时空转的几帧
            for _ in range(int(rate / CHUNK * 0.3) or 1):
                try:
                    stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    return None

            vals: list[float] = []
            deadline = time.time() + PROBE_SECONDS
            while time.time() < deadline:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    break
                vals.append(_rms16(data))
            if not vals:
                return None

            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            return {
                "mean": mean,
                "std": math.sqrt(var),
                "peak": max(vals),
                "floor": min(vals),
                "frames": float(len(vals)),
            }
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
    finally:
        try:
            p.terminate()
        except Exception:
            pass


def main() -> int:
    try:
        import pyaudio as pa
    except ImportError:
        print("未安装 pyaudio")
        return 1

    p = pa.PyAudio()
    try:
        try:
            default_index = int(p.get_default_input_device_info()["index"])
        except Exception:
            default_index = -1
        devices = []
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            devices.append((i, str(info.get("name", "")), i == default_index))
    finally:
        p.terminate()

    print("麦克风体检（每条通道采样 1.5 秒）\n")
    header = f"{'dev':>4} | {'默认':<4} | {'rate':>5} | {'均值':>9} | {'标准差':>9} | {'峰值':>9} | {'最小值':>9} | 名称"
    print(header)
    print("-" * len(header))

    for index, name, is_default in devices:
        for rate in RATE_TRIES:
            res = probe(index, rate)
            if res is None:
                continue
            print(
                f"{index:>4} | {'是' if is_default else '':<4} | {rate:>5} | "
                f"{res['mean']:>9.5f} | {res['std']:>9.5f} | "
                f"{res['peak']:>9.5f} | {res['floor']:>9.5f} | {name[:40]}"
            )
            break
        else:
            print(f"{index:>4} | {'是' if is_default else '':<4} | {'-':>5} | "
                  f"{'-':>9} | {'-':>9} | {'-':>9} | {'-':>9} | {name[:40]} (打不开)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
