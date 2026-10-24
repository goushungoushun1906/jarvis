"""逐个输入通道录同样长的音频，用于横向 A/B 对比唤醒效果。

背景：系统里同一个「英特尔® 智音」阵列麦在 PyAudio 里暴露成多条设备（不同
host API / 不同原生采样率 / 不同声道数）。之前一直固定用 idx=5，而它是
6 声道、原生 44100Hz，被强行按 16kHz 单声道打开 —— 多声道降混加重采样，
很可能就是唤醒模型识别不了的原因。

用法（server 目录）：
    .venv\\Scripts\\python.exe scripts/dev/record_all_devices.py [每路秒数]

跑的时候请对着麦克风**持续重复唤醒词**，每路录完会自动切下一路。
"""

import os
import sys
import time
import wave
import array
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pyaudio  # noqa: E402

OUT_DIR = os.path.join("logs", "wakeword-devices")
TARGET_RATE = 16000


def record_device(pa, index, name, seconds, host_api):
    """返回 (rms, peak, 实际采样率, 声道数) 或 None（打不开）"""
    dev = pa.get_device_info_by_index(index)
    native = int(dev["defaultSampleRate"])
    max_ch = int(dev["maxInputChannels"])

    # 优先按 16k 打开（引擎要的就是 16k）；失败再退到原生采样率
    for rate in (TARGET_RATE, native):
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=rate,
                input=True,
                frames_per_buffer=1024,
                input_device_index=index,
            )
            break
        except Exception:
            stream = None
    if stream is None:
        return None

    buf = bytearray()
    lock = threading.Lock()

    def _cb(in_data, frame_count, time_info, status):
        with lock:
            buf.extend(in_data)
        return (None, pyaudio.paContinue)

    # 重开为回调模式，保证按真实时钟采集
    stream.close()
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            input=True,
            frames_per_buffer=1024,
            input_device_index=index,
            stream_callback=_cb,
        )
    except Exception as e:
        return None

    stream.start_stream()
    time.sleep(seconds)
    stream.stop_stream()
    stream.close()

    with lock:
        raw = bytes(buf)
    if not raw:
        return None

    a = array.array("h")
    a.frombytes(raw)
    rms = (sum((x / 32768.0) ** 2 for x in a) / max(1, len(a))) ** 0.5
    peak = max(abs(x) for x in a) / 32768.0

    tag = f"dev{index:02d}_rate{rate}_ch{max_ch}"
    with wave.open(os.path.join(OUT_DIR, f"{tag}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)
    return rms, peak, rate, max_ch


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    os.makedirs(OUT_DIR, exist_ok=True)

    pa = pyaudio.PyAudio()
    # 跳过 MME 聚合通道（声音映射器 / 主声音捕获驱动程序）—— 它们只转发，不采集
    skip = {0, 4}
    devices = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if int(d.get("maxInputChannels", 0)) > 0 and i not in skip:
            devices.append((i, d["name"], int(d["defaultSampleRate"]), int(d["maxInputChannels"])))

    print(f"共 {len(devices)} 路，每路 {seconds}s，约 {len(devices)*seconds:.0f}s", flush=True)
    print("请对着麦克风持续重复唤醒词…\n", flush=True)

    results = []
    for idx, name, native, ch in devices:
        print(f"[{idx:>2d}] 录音中… {name[:46]}", end="", flush=True)
        r = record_device(pa, idx, name, seconds, None)
        if r is None:
            print("  -> 打开失败", flush=True)
            continue
        rms, peak, rate, max_ch = r
        print(f"  -> rms={rms:.4f} peak={peak:.3f} @{rate}Hz(原生{native}, {max_ch}ch)", flush=True)
        results.append((idx, name, rms, peak, rate, max_ch))

    pa.terminate()
    print("\n=== 汇总（按电平排序）===")
    for idx, name, rms, peak, rate, max_ch in sorted(results, key=lambda x: -x[2]):
        print(f"  dev{idx:>2d} rms={rms:.4f} peak={peak:.3f} @{rate}Hz {max_ch}ch  {name[:44]}")
    print(f"\n音频已存到 {OUT_DIR}")


if __name__ == "__main__":
    main()
