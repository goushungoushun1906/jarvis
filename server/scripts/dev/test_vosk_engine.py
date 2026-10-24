"""端到端验证新接入的 VoskWakeEngine。

不是测算法，是测**集成**：配置能不能自动找到模型和唤醒词、
引擎能不能按生产节奏逐帧喂入、召回与误报是否符合预期。
"""

from __future__ import annotations

import glob
import os
import re
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from app.wakeword import load_wake_config  # noqa: E402
from app.wakeword_vosk import VoskWakeEngine  # noqa: E402

RATE = 16000
D = os.path.join(BASE, "logs", "wakeword-live")

# ASR 复核确认过含唤醒词的分片
CONFIRMED_SPEECH = {
    "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14",
    "32", "33", "34", "35", "36", "37", "38", "39", "40", "41", "42", "44",
    "46", "47",
}


def load_all():
    files = sorted(glob.glob(os.path.join(D, "*.wav")))
    files = [f for f in files if re.match(r"^\d\d_", os.path.basename(f))]
    speech, noise = [], []
    for f in files:
        key = os.path.basename(f)[:2]
        with wave.open(f, "rb") as w:
            if w.getframerate() != RATE:
                continue
            raw = w.readframes(w.getnframes())
        (speech if key in CONFIRMED_SPEECH else noise).append(
            (os.path.basename(f), raw)
        )
    return speech, noise


def frames(raw: bytes, size: int = 512):
    n = len(raw) // 2
    vals = list(wave.struct.unpack(f"<{n}h", raw[: n * 2]))
    for i in range(0, len(vals) - size + 1, size):
        yield vals[i : i + size]


def main() -> int:
    cfg = load_wake_config()
    print(f"引擎      : {cfg.engine}")
    print(f"Vosk 模型 : {cfg.vosk_model}")
    print(f"唤醒词    : {cfg.keyword_names}")
    print(f"vosk_ready: {cfg.vosk_ready}")
    print()

    speech, noise = load_all()
    print(f"样本：真实人声 {len(speech)} 片，环境噪声 {len(noise)} 片\n")

    engine = VoskWakeEngine(cfg)
    print(f"语法条目（{len(engine.grammar_entries)} 条）：")
    for g in engine.grammar_entries:
        print(f"    {g}")
    print()

    tp, lat = 0, []
    for name, raw in speech:
        hit_at = None
        total = 0
        for i, fr in enumerate(frames(raw)):
            total = i
            idx, score = engine.process(fr)
            if idx >= 0 and hit_at is None:
                hit_at = i * 512 / RATE
                break
        # 每片结束强制收尾，让引擎内部状态不跨片累积
        if hit_at is None:
            idx, score = engine.flush()
            if idx >= 0:
                tp += 1
                continue
            idx2, _ = engine.flush()
        if hit_at is not None:
            tp += 1
            lat.append(hit_at)
        engine.process([0] * 512)

    fp = 0
    fp_names = []
    for name, raw in noise:
        hit = False
        for fr in frames(raw):
            idx, score = engine.process(fr)
            if idx >= 0:
                hit = True
                break
        if not hit:
            idx, _ = engine.flush()
            hit = idx >= 0
        if hit:
            fp += 1
            fp_names.append(name)
        engine.process([0] * 512)

    print("=== 结果 ===")
    print(f"召回 {tp}/{len(speech)} = {tp/len(speech)*100:.0f}%"
          if speech else "无样本")
    print(f"误报 {fp}/{len(noise)} = {fp/len(noise)*100:.0f}%"
          if noise else "")
    if lat:
        print(f"平均响应 {sum(lat)/len(lat):.2f}s")
    if fp_names:
        print(f"误报样本：{', '.join(fp_names)}")
    print()
    print("对照：sherpa 3.3M KWS 召回 2/26 = 8%")

    engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
