"""麦克风「每轮一条流」 vs 「全局共享一条流」的对照诊断。

背景
----
连续对话里第二轮开始就录不到声音。后端日志显示那几轮**帧数是满的**
（6.6s ÷ 256ms ≈ 24 帧），说明流确实在跑，可整段音频的 RMS 全在说话阈值
以下 —— 拿到的是哑数据。

根因是旧实现每录一次都新建一个 PyAudio 实例，并在**后台线程**里 terminate()。
两轮之间只隔 14 毫秒，新旧 PortAudio 实例的生命周期因此叠在一起。

这个脚本就在真机上把两种写法各跑一遍，用实测电平把结论钉死。

用法（会占用麦克风约 30 秒，安静环境下也能出结论）：:

    server\\.venv\\Scripts\\python.exe server/tools/diag_mic_sharing.py

判定：两种写法都各自测若干轮，比较**第 2 轮之后**的平均 RMS 相对第 1 轮
掉了多少。掉到接近 0 就是复现了那个 bug。
"""

from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

ROUND_S = 2.0
ROUNDS = 4
# 模拟前端的行为：上一轮 /record/stop 返回后只隔这么点时间就发起下一轮
GAP_S = 0.014


def _rms(raw: bytes) -> float:
    if not raw:
        return 0.0
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _peak(raw: bytes) -> float:
    if not raw:
        return 0.0
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.max(np.abs(samples))) if samples.size else 0.0


def _pick_index() -> int | None:
    from app.mic_selector import pick_input_device

    return pick_input_device().get("index")


def legacy_round(index: int | None) -> tuple[float, float, int]:
    """旧写法：一次录音 = 一个 PyAudio 实例 + 两条流 + 后台线程 terminate。"""
    import pyaudio as pa

    chunks: list[bytes] = []
    finished = threading.Event()

    p = pa.PyAudio()
    kwargs = dict(
        format=pa.paInt16, channels=1, rate=16000,
        input=True, frames_per_buffer=4096,
    )
    if index is not None:
        kwargs["input_device_index"] = index
    try:
        _probe = p.open(**kwargs)          # ← 第一条流：只用来试探，从不关闭
    except Exception:
        kwargs.pop("input_device_index", None)
        _probe = p.open(**kwargs)

    def _cb(in_data, frame_count, time_info, status):
        chunks.append(in_data)
        return (None, pa.paContinue)

    stream = p.open(**kwargs, stream_callback=_cb)
    stream.start_stream()
    time.sleep(ROUND_S)

    # 收尾线程：不 join，就和旧实现一样让它在后面慢慢 terminate
    def _finish():
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        finished.set()

    threading.Thread(target=_finish, daemon=True).start()
    raw = b"".join(chunks)
    return _rms(raw), _peak(raw), len(chunks)


def shared_round(index: int | None) -> tuple[float, float, int]:
    """新写法：全局只有一条流，录音只是挂个监听者。"""
    from app import mic_capture

    chunks: list[bytes] = []
    remove = mic_capture.add_listener(chunks.append, index)
    try:
        time.sleep(ROUND_S)
    finally:
        remove()
    raw = b"".join(chunks)
    return _rms(raw), _peak(raw), len(chunks)


def run(title: str, fn) -> list[tuple[float, float, int]]:
    print(f"\n=== {title} ===")
    results = []
    for i in range(ROUNDS):
        rms, peak, frames = fn()
        results.append((rms, peak, frames))
        print(f"  第 {i + 1} 轮：rms={rms:.5f}  peak={peak:.5f}  帧数={frames}")
        time.sleep(GAP_S)
    mic_capture_shutdown()
    return results


_shutdown_done = False


def mic_capture_shutdown() -> None:
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    try:
        from app import mic_capture

        mic_capture.shutdown()
    except Exception:
        pass


def report(name: str, rows: list[tuple[float, float, int]]) -> None:
    first = rows[0][0]
    later = [r[0] for r in rows[1:]]
    avg_later = sum(later) / len(later) if later else 0.0
    if first > 0:
        drop = (1 - avg_later / first) * 100
    else:
        drop = 0.0
    print(f"\n{name}：第 1 轮 rms={first:.5f}，第 2 轮起均值={avg_later:.5f}"
          f"，衰减 {drop:.1f}%")
    if frames_are_missing(rows):
        print(f"  ⚠ 有轮次一帧都没收到（不只是音量低）")
    flag = "❌ 后续轮次明显变哑 —— 复现了 bug" if drop > 60 else "✅ 后续轮次电平稳定"
    print(f"  {flag}")


def frames_are_missing(rows: list[tuple[float, float, int]]) -> bool:
    return any(r[2] == 0 for r in rows)


def main() -> int:
    print("麦克风共享诊断：每轮录 %.1f 秒，共 %d 轮，轮间隔 %.0f ms"
          % (ROUND_S, ROUNDS, GAP_S * 1000))
    print("提示：安静环境下也会有 ~0.0005~0.005 的本底噪声；"
          "若某轮读到 0.0000 或一帧都没有，就说明那条流是哑的。")

    index = _pick_index()
    print(f"\n自动选中设备 index={index}")

    legacy = run("旧写法：每轮一个 PyAudio 实例（对照）", lambda: legacy_round(index))
    shared = run("新写法：全局共享一条流（当前实现）", lambda: shared_round(index))

    print("\n" + "=" * 56)
    report("旧写法", legacy)
    report("新写法", shared)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
