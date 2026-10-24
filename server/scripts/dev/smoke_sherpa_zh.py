"""中文唤醒词实喊冒烟测试。

离线回放只能证明"模型能认出标准音频"，证明不了"你喊一声它能听见"。
这一步必须真人参与 —— 麦克风素质、发音习惯、环境噪声都只有实跑才暴露。

用法：
    python scripts/dev/smoke_sherpa_zh.py              # 默认 20 轮
    python scripts/dev/smoke_sherpa_zh.py --times 5    # 快速试一下
    python scripts/dev/smoke_sherpa_zh.py --threshold 0.2   # 调阈值重测

判定建议：
    漏检 ≤ 1 次、安静期误报 ≤ 1 次、单帧耗时 < 32ms 视为可用。
    漏检多 → 调小阈值（或把 keywords.txt 里的 boost 调大）；
    误报多 → 调大阈值。
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app import sherpa_runtime  # noqa: E402,F401  副作用：注册 DLL 目录
from app.wakeword import (  # noqa: E402
    SherpaEngine,
    load_wake_config,
    threshold_from_sensitivity,
)

RING_NOTE = (
    "提示：内置麦克风请先在 Windows 声音设置里关闭「音频增强 / 自动增益」。\n"
    "      它会改变输入信号特征，是很多'识别不准'的真正原因。\n"
)


def wait_enter(prompt: str) -> None:
    try:
        input(prompt)
    except EOFError:
        print("(非交互环境，直接继续)")


def main() -> int:
    parser = argparse.ArgumentParser(description="中文唤醒词实喊冒烟")
    parser.add_argument("--times", type=int, default=20, help="实喊轮数")
    parser.add_argument("--threshold", type=float, default=None, help="覆盖触发阈值")
    parser.add_argument("--rounds-silence", type=float, default=10.0, help="安静期观察秒数")
    args = parser.parse_args()

    cfg = load_wake_config()
    if cfg.engine != "sherpa":
        print(f"当前引擎是 {cfg.engine}，本脚本只针对 sherpa。")
        print("请设置 JARVIS_WAKE_ENGINE=sherpa 后重试。")
        return 1
    if not cfg.sherpa_ready:
        print("模型未就绪，请先运行： python scripts/dev/download_sherpa_kws.py")
        return 1

    if args.threshold is not None:
        cfg.sherpa_threshold = args.threshold

    print("=" * 56)
    print("中文唤醒词实喊冒烟")
    print("=" * 56)
    print(f"唤醒词   : {', '.join(cfg.keyword_names) or '(未配置)'}")
    print(f"触发阈值 : {cfg.sherpa_threshold}  (灵敏度推算值)")
    print(f"参考换算 : 灵敏度 -> 阈值 = {threshold_from_sensitivity(0.7)} @0.70")
    print(f"实喊轮数 : {args.times}")
    print()
    print(RING_NOTE)

    try:
        import pyaudio
    except ImportError:
        print("缺少 pyaudio，无法录音。请在 dev 虚拟环境中安装。")
        return 1

    try:
        engine = SherpaEngine(cfg)
    except Exception as e:
        print(f"引擎启动失败：{e}")
        return 1

    pa = pyaudio.PyAudio()
    stream = None
    hits = 0
    misses = 0
    frame_times: list[float] = []

    try:
        stream = pa.open(
            rate=engine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=engine.frame_length,
        )

        wait_enter("准备好了按回车开始（之后每轮听到提示音再喊）...\n")
        print()

        for i in range(1, args.times + 1):
            print(f"--- 第 {i}/{args.times} 轮 ---  现在请喊：「{cfg.keyword_names[0] if cfg.keyword_names else '?' }」")
            deadline = time.time() + 4.0
            got = False
            while time.time() < deadline:
                try:
                    pcm = stream.read(engine.frame_length, exception_on_overflow=False)
                except Exception:
                    break
                import struct

                samples = struct.unpack(f"<{engine.frame_length}h", pcm)
                t0 = time.perf_counter()
                index, score = engine.process(samples)
                frame_times.append((time.perf_counter() - t0) * 1000)
                if index >= 0:
                    got = True
                    break
            if got:
                hits += 1
                print(f"    命中 (score={score:.2f})")
            else:
                misses += 1
                print("    未命中")

        print(f"\n--- 安静期 {args.rounds_silence:.0f} 秒，请不要说话 ---")
        false_alarms = 0
        end = time.time() + args.rounds_silence
        while time.time() < end:
            try:
                pcm = stream.read(engine.frame_length, exception_on_overflow=False)
            except Exception:
                break
            import struct

            samples = struct.unpack(f"<{engine.frame_length}h", pcm)
            index, _ = engine.process(samples)
            if index >= 0:
                false_alarms += 1
                print("    安静期误触发！")
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()
        engine.close()

    print("\n" + "=" * 56)
    print("结果")
    print("=" * 56)
    print(f"命中       : {hits}/{args.times}")
    print(f"漏检       : {misses}")
    if frame_times:
        p50 = statistics.median(frame_times)
        p95 = sorted(frame_times)[max(0, int(len(frame_times) * 0.95) - 1)]
        print(f"单帧耗时   : P50 {p50:.2f}ms / P95 {p95:.2f}ms")
    print(f"安静期误报 : {false_alarms}")

    ok = True
    print()
    if misses > 1:
        ok = False
        print(f"  漏检 {misses} 次偏多。建议：把阈值调小（当前 {cfg.sherpa_threshold}），"
              f"或调大 keywords.txt 里的 boost。")
    if false_alarms > 1:
        ok = False
        print(f"  安静期误报 {false_alarms} 次偏多。建议：把阈值调大（当前 {cfg.sherpa_threshold}）。")
    if frame_times and sorted(frame_times)[int(len(frame_times) * 0.95) - 1] >= 32:
        print("  单帧耗时偏高，检查一下是不是在省电模式。")

    print("\n总体：" + ("可用" if ok else "需要调参"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
