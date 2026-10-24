"""直接用「生产环境的 SherpaEngine 封装」跑音频，验证封装层是否有 bug。

之前所有测试都是直接调裸 sherpa_onnx.KeywordSpotter，命中率 100%（历史捕获）
vs 4%（实时录音）。但生产跑的是我们自己的 SherpaEngine 封装——它多做了三件事：
  1. 双实例（主 spotter + 严格 spotter），两者喂同一帧
  2. 自己算 boost / threshold
  3. get_result() 放在 decode 循环外面，每帧只取一次

如果封装层有问题，就只会表现为"实时唤不醒"，而裸引擎测试完全正常。
这个脚本就是补上这块空白。
"""

from __future__ import annotations

import os
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

import numpy as np  # noqa: E402

from app.wakeword import SherpaEngine, load_wake_config  # noqa: E402
FRAME = 512


def load(path: str) -> np.ndarray:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def run_engine(engine, a: np.ndarray, label: str) -> None:
    """按生产的节奏逐帧喂入（每帧 512 采样 = 32ms）。"""
    pcm_all = (a * 32768.0).astype(np.int16)
    hits = []
    for i in range(0, len(pcm_all) - FRAME + 1, FRAME):
        idx, score = engine.process(pcm_all[i : i + FRAME].tolist())
        if idx >= 0:
            hits.append((i / 16000, score))
    if hits:
        detail = ", ".join(f"{t:.2f}s(score {s})" for t, s in hits)
        print(f"  [生产封装] {label:<34s} ✅ 命中  {detail}")
    else:
        print(f"  [生产封装] {label:<34s} ❌ 未命中")


def main() -> int:
    cfg = load_wake_config()
    print(f"配置: boost={cfg.sherpa_boost}  threshold(推导)见下  "
          f"threads={cfg.sherpa_num_threads} provider={cfg.sherpa_provider}")
    print(f"关键词文件: {cfg.sherpa_keywords_src}\n")

    targets = [
        ("历史捕获 203116（裸引擎命中）", "logs/wakeword/20261014-203116-你好贾维斯-100.wav"),
        ("历史捕获 070014", "logs/wakeword/20261014-070014-你好贾维斯-100.wav"),
        ("实时 06（裸引擎未命中）", "logs/wakeword-live/06_rms0.0112_peak0.142.wav"),
        ("实时 03（裸引擎未命中）", "logs/wakeword-live/03_rms0.0089_peak0.081.wav"),
        ("实时 11（裸引擎命中）", "logs/wakeword-live/11_rms0.0084_peak0.059.wav"),
        ("实时 14（裸引擎命中）", "logs/wakeword-live/14_rms0.0064_peak0.052.wav"),
        ("回放 dev04", "logs/wakeword-ab/dev04_rms0.0992_peak0.950.wav"),
        ("回放 dev05", "logs/wakeword-ab/dev05_rms0.0997_peak0.950.wav"),
    ]

    for label, rel in targets:
        p = os.path.join(BASE, rel)
        if not os.path.isfile(p):
            print(f"  {label:<34s} (文件不存在)")
            continue
        # 每个样本用全新引擎，避免状态串扰（生产里也是一条流跑到底，
        # 这里先确认"新引擎"这一档）
        engine = SherpaEngine(cfg)
        try:
            run_engine(engine, load(p), label)
        finally:
            engine.close()

    print()
    print("对照：如果历史捕获在「生产封装」下也不命中，就说明封装层有 bug，")
    print("      而不是麦克风或音频的问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
