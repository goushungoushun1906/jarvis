"""用「扬声器回放 + 逐通道录音」自动化做麦克风通道 A/B 测试。

为什么这么干：靠用户卡时间喊太不可控（喊早了/喊晚了都白录）。
把同一段真实人声从扬声器放出来，逐条通道录一遍，声学输入完全相同，
于是各通道的差异就纯粹反映通道本身的质量。

判据：对每条通道的录音跑 KWS（唤醒）与 ASR（识别），
      - ASR 出「你好贾维斯」 → 音频可懂，通道能用
      - KWS 命中            → 这条通道能让唤醒引擎工作
"""

from __future__ import annotations

import array
import os
import sys
import time
import wave

os.environ.setdefault("PYTHONUTF8", "1")

import pyaudio  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REF_SRC = os.path.join(
    BASE, "logs", "wakeword", "20261014-203116-你好贾维斯-100.wav"
)
REF_PLAY = os.path.join(BASE, "logs", "wakeword-live", "_ref_play.wav")
OUT_DIR = os.path.join(BASE, "logs", "wakeword-ab")
RATE = 16000
CHUNK = 1024
GAP_S = 1.2          # 每次播放之间的间隔
PLAY_TIMES = 3       # 每条通道播放次数


def load(path: str) -> array.array:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return a


def save(a: array.array, path: str) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())


def make_ref() -> array.array:
    """把真实录音放大到接近满刻度，保证扬声器回放有足够声压。"""
    a = load(REF_SRC)
    peak = max(abs(x) for x in a) or 1
    gain = (0.95 * 32767) / peak
    b = array.array("h", [max(-32767, min(32767, int(x * gain))) for x in a])
    save(b, REF_PLAY)
    print(f"回放源: {os.path.basename(REF_SRC)}  原峰值 {peak/32768:.3f} -> 放大 {gain:.1f}x")
    return b


def build_playlist(ref: array.array) -> array.array:
    """重复 PLAY_TIMES 次，中间插静音，前后留一点余量。"""
    gap = array.array("h", [0] * int(RATE * GAP_S))
    lead = array.array("h", [0] * int(RATE * 0.5))
    tail = array.array("h", [0] * int(RATE * 0.5))
    out = array.array("h", lead)
    for i in range(PLAY_TIMES):
        out.extend(ref)
        if i < PLAY_TIMES - 1:
            out.extend(gap)
    out.extend(tail)
    return out


def record_on_device(pa: pyaudio.PyAudio, dev_index: int, playlist: array.array):
    """在指定通道上录音，同时把 playlist 从默认扬声器放出来。"""
    total = len(playlist)
    rec = array.array("h")

    try:
        out_stream = pa.open(
            format=pyaudio.paInt16, channels=1, rate=RATE, output=True,
            frames_per_buffer=CHUNK,
        )
    except Exception as e:
        return None, f"输出设备打开失败: {e}"

    try:
        in_stream = pa.open(
            format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
            input_device_index=dev_index, frames_per_buffer=CHUNK,
        )
    except Exception as e:
        try:
            out_stream.close()
        except Exception:
            pass
        return None, f"输入设备打开失败: {e}"

    sent = 0
    try:
        while sent < total:
            chunk = playlist[sent : sent + CHUNK]
            if len(chunk) < CHUNK:
                chunk = array.array(
                    "h", list(chunk) + [0] * (CHUNK - len(chunk))
                )
            out_stream.write(chunk.tobytes())
            sent += CHUNK
            try:
                data = in_stream.read(CHUNK, exception_on_overflow=False)
                rec.extend(array.array("h", data))
            except Exception:
                rec.extend(array.array("h", [0] * CHUNK))
        # 多读一点，把回放尾巴收进来
        for _ in range(int(RATE * 0.3 / CHUNK) + 1):
            try:
                data = in_stream.read(CHUNK, exception_on_overflow=False)
                rec.extend(array.array("h", data))
            except Exception:
                break
    finally:
        for s in (out_stream, in_stream):
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass

    return rec, None


def stat(a: array.array) -> tuple[float, float]:
    rms = (sum((x / 32768.0) ** 2 for x in a) / max(1, len(a))) ** 0.5
    peak = max(abs(x) for x in a) / 32768.0
    return rms, peak


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isfile(REF_SRC):
        print(f"回放源不存在: {REF_SRC}")
        return 1

    ref = make_ref()
    playlist = build_playlist(ref)
    print(f"每条通道回放 {PLAY_TIMES} 次，单次时长 {len(playlist)/RATE:.1f}s\n")

    pa = pyaudio.PyAudio()

    # 枚举输入设备
    devices = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            devices.append(
                (i, d["name"], int(d["defaultSampleRate"]), int(d["maxInputChannels"]))
            )

    print(f"共 {len(devices)} 条输入通道，逐条测试：\n")
    print(f"{'idx':>4s}  {'原生率':>7s} {'ch':>2s}  {'rms':>8s} {'peak':>7s}  结果")
    print("-" * 78)

    results = []
    for idx, name, native, ch in devices:
        rec, err = record_on_device(pa, idx, playlist)
        if err:
            print(f"{idx:>4d}  {native:>7d} {ch:>2d}  {'—':>8s} {'—':>7s}  {err}")
            continue
        rms, peak = stat(rec)
        path = os.path.join(OUT_DIR, f"dev{idx:02d}_rms{rms:.4f}_peak{peak:.3f}.wav")
        save(rec, path)
        print(f"{idx:>4d}  {native:>7d} {ch:>2d}  {rms:>8.5f} {peak:>7.3f}  {name[:36]}")
        results.append((idx, name, path, rms, peak))

    pa.terminate()

    print(f"\n音频已存到: {OUT_DIR}")
    print("下一步：对每条通道的录音分别跑 KWS 与 ASR，看哪条能让唤醒引擎工作。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
