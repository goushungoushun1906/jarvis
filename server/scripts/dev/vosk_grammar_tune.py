"""调优 Vosk 唤醒语法：加入同音字变体，看召回率能推到多高。

思路：语法锁死搜索空间的同时，也把"用户实际发音的变体"排除在外了。
      既然 ASR 听出过「贾维斯 / 加维斯 / 小维斯 / 叫维斯」，
      就把这些同音变体一并写进语法，让解码器自己挑最接近的。

风险：变体越多越容易误报，所以必须同时盯住误报率。
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RATE = 16000
D = os.path.join(BASE, "logs", "wakeword-live")
MODEL = os.path.join(BASE, "models", "vosk", "vosk-model-small-cn-0.22")

# ASR 复核确认过的真实人声片（含轻声那 4 片）
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
        (speech if key in CONFIRMED_SPEECH else noise).append((os.path.basename(f), raw))
    return speech, noise


GRAMMAR_SETS = {
    "G1 基准：仅标准写法": [
        "你 好 贾 维 斯",
    ],
    "G2 加同音字变体": [
        "你 好 贾 维 斯",
        "你 好 加 维 斯",
        "你 好 假 维 斯",
        "你 好 家 维 斯",
        "你 好 小 维 斯",
        "你 好 叫 维 斯",
        "贾 维 斯",
        "加 维 斯",
    ],
    "G3 变体+带语气词": [
        "你 好 贾 维 斯",
        "你 好 加 维 斯",
        "你 好 假 维 斯",
        "你 好 家 维 斯",
        "你 好 小 维 斯",
        "你 好 叫 维 斯",
        "你 好 贾 维 斯 啊",
        "贾 维 斯",
        "加 维 斯",
        "维 斯",
    ],
}


def run_grammar(grammar: list[str], speech, noise):
    from vosk import KaldiRecognizer, Model

    model = Model(MODEL)
    g = json.dumps(grammar + ["[unk]"], ensure_ascii=False)

    def rec_one(raw: bytes) -> str:
        rec = KaldiRecognizer(model, RATE, g)
        step = 1024
        for i in range(0, len(raw), step):
            rec.AcceptWaveform(raw[i : i + step])
        return (json.loads(rec.FinalResult()).get("text") or "").replace(" ", "")

    def hit(text: str) -> bool:
        return any(k in text for k in ("维斯", "维思", "为斯", "维 斯"))

    tp = sum(1 for _, raw in speech if hit(rec_one(raw)))
    fp = sum(1 for _, raw in noise if hit(rec_one(raw)))
    return tp, fp


def main() -> int:
    speech, noise = load_all()
    print(f"样本：真实人声 {len(speech)} 片（ASR 已复核），环境噪声 {len(noise)} 片")
    print("对照：sherpa 3.3M KWS 召回 2/26 = 8%\n")
    print(f"{'语法方案':<24s} {'召回':>12s} {'误报':>12s}")
    print("-" * 52)

    for name, grammar in GRAMMAR_SETS.items():
        tp, fp = run_grammar(grammar, speech, noise)
        r = tp / len(speech) * 100 if speech else 0
        f = fp / len(noise) * 100 if noise else 0
        print(f"{name:<24s} {tp:>4d}/{len(speech)} = {r:>3.0f}% {fp:>4d}/{len(noise)} = {f:>3.0f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
