"""录一段真实麦克风音频，用于离线对比 KWS / ASR。

用法（server 目录）：
    .venv\\Scripts\\python.exe scripts/dev/record_live.py [总秒数] [分片秒数]

默认录 30 秒，每 3 秒存一个 wav 到 logs/wakeword-live/<序号>_rms<值>.wav，
并打印每片的 RMS / 峰值，方便挑出有声音的那一片。

踩过的坑（别改回去）：
  上一版用 `stream.read(512)` 阻塞式读取，在英特尔阵列麦上 read() 不会按实时
  节奏阻塞，100 秒内跑出了 8000 个分片（每片 rms 都是 0.095）。改用回调模式
  （PyAudio 的标准实时写法），由 PortAudio 按真实时钟驱动，分片数才是正确的。
  另外录制目录刻意与 logs/wakeword 分开 —— 后者是引擎命中时保存的捕获，
  被这里污染会让 kws_diag / kws_sweep 的 glob 抓到一堆无关文件。

**跑之前务必先停掉唤醒引擎**（POST /api/wakeword/stop），否则麦克风被占用。
"""

import os
import sys
import time
import wave
import array
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pyaudio  # noqa: E402

from app import mic_selector  # noqa: E402

OUT_DIR = os.path.join("logs", "wakeword-live")
RATE = 16000


def main():
    total = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    chunk_s = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    pick = mic_selector.pick_input_device()
    index = pick.get("index")
    print(f"[选择] device={index} name={pick.get('name')}", flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)

    lock = threading.Lock()
    buf = bytearray()

    def _cb(in_data, frame_count, time_info, status):
        with lock:
            buf.extend(in_data)
        return (None, pyaudio.paContinue)

    pa = pyaudio.PyAudio()
    kwargs = dict(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        frames_per_buffer=1024,
        stream_callback=_cb,
    )
    if index is not None:
        kwargs["input_device_index"] = index

    stream = pa.open(**kwargs)
    stream.start_stream()
    print(f"[就绪] 录音 {total}s，分片 {chunk_s}s，请说话…", flush=True)

    t_end = time.time() + total
    idx = 0
    try:
        while time.time() < t_end:
            time.sleep(chunk_s)
            with lock:
                raw = bytes(buf)
                buf.clear()
            if not raw:
                continue
            a = array.array("h")
            a.frombytes(raw)
            rms = (sum((x / 32768.0) ** 2 for x in a) / max(1, len(a))) ** 0.5
            peak = max(abs(x) for x in a) / 32768.0
            name = f"{idx:02d}_rms{rms:.4f}_peak{peak:.3f}.wav"
            with wave.open(os.path.join(OUT_DIR, name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(raw)
            print(f"[{idx:02d}] rms={rms:.5f} peak={peak:.3f} -> {name}", flush=True)
            idx += 1
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
    print(f"[完成] 共 {idx} 片，目录 {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
