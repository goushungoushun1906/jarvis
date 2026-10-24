"""中文唤醒词实测：用真实麦克风跑 N 次，量出漏检、误报与延迟。

为什么要有这一步
----------------
换引擎之前必须先证明新引擎在自己这台机器、自己这张嘴上真的好用。
我不在你身边，无法替你喊，所以这一步只能你跑 —— 但脚本会替你记录和判定。

它会量三件事：

1. 漏检率：喊了没反应。
2. 误报率：没喊却自己醒了（安静时段内出现的命中都算）。
3. 引擎单帧处理耗时：这是 KWS 自身的开销，能稳定在远低于一帧（32ms）
   之下，才说明"响应快"这件事是引擎给的，而不是偶然。

关于"延迟"的说明：脚本报的「响应延迟」包含了你的反应时间（从提示出现到你
开口），因此看起来会有零点几秒到一秒多，这是正常的。真正能代表引擎速度的
是「单帧处理耗时」那一项。

用法：
    python server/scripts/dev/smoke_porcupine_zh.py [--times 20] [--sensitivity 0.6]

前置条件：
    - .env 里已填 PORCUPINE_ACCESS_KEY
    - server/models/wakeword/ 下有 porcupine_params_zh.pv 和自训练的 .ppn
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, SERVER_DIR)

# 达标线（对应 docs/WAKE_WORD_DESIGN.md 的验收标准）
MAX_MISSES = 1
MAX_FALSE_ALARMS = 1
FRAME_BUDGET_MS = 32.0     # 16kHz 下每帧 32ms，处理必须快于它才不会积压
QUIET_SECONDS = 2.0        # 每轮之间的安静期，用来观察误报
LISTEN_TIMEOUT_S = 6.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--times", type=int, default=20, help="测试轮数")
    parser.add_argument("--sensitivity", type=float, default=None,
                        help="覆盖灵敏度（0..1，越高越容易触发）")
    parser.add_argument("--device", type=int, default=None, help="麦克风设备下标")
    args = parser.parse_args()

    from app.wakeword import PorcupineEngine, load_wake_config

    cfg = load_wake_config()
    if args.sensitivity is not None:
        cfg.sensitivities = [args.sensitivity] * max(1, len(cfg.sensitivities))
    if args.device is not None:
        cfg.device_index = args.device

    if not cfg.access_key:
        print("未检测到 AccessKey。请先在 server/.env 中填写：")
        print("    PORCUPINE_ACCESS_KEY=<你的密钥>")
        return 2
    if not cfg.keyword_paths:
        print("未找到关键词模型（.ppn）。请把自训练的 ppn 放到：")
        print("    server/models/wakeword/")
        return 2
    if not os.path.isfile(cfg.model_path):
        print(f"缺少中文声学参数：{cfg.model_path}")
        print("从 https://github.com/Picovoice/porcupine/raw/master/lib/common/"
              "porcupine_params_zh.pv 下载后放到 server/models/wakeword/")
        return 2

    try:
        engine = PorcupineEngine(cfg)
    except Exception as e:
        print(f"引擎启动失败：{e}")
        return 2

    import pyaudio as pa

    py_audio = pa.PyAudio()
    try:
        stream = py_audio.open(
            format=pa.paInt16,
            channels=1,
            rate=engine.sample_rate,
            input=True,
            frames_per_buffer=engine.frame_length,
        )
    except Exception as e:
        print(f"打开麦克风失败：{e}")
        py_audio.terminate()
        engine.close()
        return 2

    keyword = cfg.keyword_names[0] if cfg.keyword_names else "唤醒词"
    print(f"\n引擎就绪：{engine.name}　采样率 {engine.sample_rate}　"
          f"帧长 {engine.frame_length}　灵敏度 {cfg.sensitivities}")
    print(f"唤醒词：{keyword}")
    print(f"\n接下来会进行 {args.times} 轮。每轮出现「请说」后开口，"
          f"其余时间请保持安静。\n")

    hits = 0
    misses = 0
    false_alarms = 0
    latencies: list[float] = []
    frame_costs: list[float] = []
    formatter = struct.Struct(f"<{engine.frame_length}h")

    def drain(seconds: float, stop_on_hit: bool = False) -> bool:
        """读若干秒音频。stop_on_hit=True 时命中即返回 True。"""
        deadline = time.time() + seconds
        hit = False
        while time.time() < deadline:
            raw = stream.read(engine.frame_length, exception_on_overflow=False)
            started = time.perf_counter()
            try:
                index, _score = engine.process(formatter.unpack(raw))
            except Exception:
                continue
            frame_costs.append((time.perf_counter() - started) * 1000)
            if index >= 0:
                hit = True
                if stop_on_hit:
                    break
        return hit

    try:
        for round_no in range(1, args.times + 1):
            # 安静期：这里出现的命中都是误报
            print(f"  … 安静 {QUIET_SECONDS:.0f} 秒（请勿说话）", end="\r")
            if drain(QUIET_SECONDS):
                false_alarms += 1
                print(f"  ! 第 {round_no} 轮前检测到误报            ")

            print(f"  第 {round_no}/{args.times} 轮：请说「{keyword}」"
                  f"                    ", end="\r")
            started = time.time()
            got = drain(LISTEN_TIMEOUT_S, stop_on_hit=True)
            elapsed = time.time() - started
            if got:
                hits += 1
                latencies.append(elapsed)
                print(f"  第 {round_no}/{args.times} 轮：命中 "
                      f"（{elapsed:.2f}s，含你的反应时间）        ")
            else:
                misses += 1
                print(f"  第 {round_no}/{args.times} 轮：未命中        ")
    finally:
        stream.stop_stream()
        stream.close()
        py_audio.terminate()
        engine.close()

    print("\n──────── 结果 ────────")
    print(f"命中      ：{hits}/{args.times}")
    print(f"漏检      ：{misses}（达标线 ≤{MAX_MISSES}）")
    print(f"误报      ：{false_alarms}（达标线 ≤{MAX_FALSE_ALARMS}）")
    if latencies:
        print(f"响应延迟  ：平均 {sum(latencies)/len(latencies):.2f}s，"
              f"最大 {max(latencies):.2f}s（含反应时间）")
    if frame_costs:
        avg = sum(frame_costs) / len(frame_costs)
        print(f"单帧耗时  ：平均 {avg:.3f}ms，最大 {max(frame_costs):.3f}ms"
              f"（预算 {FRAME_BUDGET_MS}ms）")

    problems: list[str] = []
    if misses > MAX_MISSES:
        problems.append(f"漏检 {misses} 次，超出达标线 —— 建议调高灵敏度或重新训练唤醒词")
    if false_alarms > MAX_FALSE_ALARMS:
        problems.append(f"误报 {false_alarms} 次 —— 建议降低灵敏度")
    if frame_costs and sum(frame_costs) / len(frame_costs) > FRAME_BUDGET_MS:
        problems.append("单帧处理超过一帧时长，会造成音频积压 —— 建议关闭双阈值打分")

    print()
    if problems:
        for p in problems:
            print(f"未达标：{p}")
        return 1
    print("全部达标，可以进入下一步。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
