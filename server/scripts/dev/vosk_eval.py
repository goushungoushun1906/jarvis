"""全面评估 Vosk 语法唤醒：召回率 + 误报率 + 响应延迟。

光看召回率不够——误唤醒比不唤醒更恼人。所以这里把 50 片实时录音
按「有人声 / 无人声」分开统计：
  - 有人声片（22 片）：应当命中 → 算召回率
  - 无人声片（28 片）：都是环境噪声，不应命中 → 算误报率
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RATE = 16000
D = os.path.join(BASE, "logs", "wakeword-live")

MODELS = [
    ("small-cn-0.22", os.path.join(BASE, "models", "vosk", "vosk-model-small-cn-0.22")),
] + (
    [("cn-0.22", os.path.join(BASE, "models", "vosk", "vosk-model-cn-0.22"))]
    if "--big" in sys.argv
    else []
)

KEYWORDS = ["你 好 贾 维 斯"]
GRAMMAR = json.dumps(KEYWORDS + ["[unk]"], ensure_ascii=False)
STRICT = json.dumps(KEYWORDS, ensure_ascii=False)


def load_all():
    files = sorted(glob.glob(os.path.join(D, "*.wav")))
    files = [f for f in files if re.match(r"^\d\d_", os.path.basename(f))]
    voiced, silent = [], []
    for f in files:
        with wave.open(f, "rb") as w:
            if w.getframerate() != RATE:
                continue
            raw = w.readframes(w.getnframes())
        n = len(raw) // 2
        vals = list(wave.struct.unpack(f"<{n}h", raw[: n * 2]))
        rms = (sum(v * v for v in vals) / n) ** 0.5 / 32768.0
        (voiced if rms >= 0.004 else silent).append((os.path.basename(f), raw))
    return voiced, silent


def make_rec(model_path: str, grammar: str):
    from vosk import KaldiRecognizer, Model

    return KaldiRecognizer(Model(model_path), RATE, grammar)


def run_streaming(rec, raw: bytes, chunk_samples: int = 512) -> tuple[str, float]:
    """按实时节奏逐块喂入，返回 (识别文本, 首次命中的时刻秒)。

    用 512 采样/块是为了模拟生产环境 32ms 一帧的节奏，
    顺便量出"说完多久能判定出来"。
    """
    step = chunk_samples * 2
    hit_at = None
    for i in range(0, len(raw), step):
        rec.AcceptWaveform(raw[i : i + step])
        if hit_at is None:
            r = json.loads(rec.PartialResult())
            p = (r.get("partial") or "").replace(" ", "")
            if "维斯" in p or "维思" in p or "为斯" in p:
                hit_at = (i + step) / step * chunk_samples / RATE
    res = json.loads(rec.FinalResult())
    text = (res.get("text") or "").replace(" ", "")
    return text, (hit_at if hit_at is not None else -1.0)


def match(text: str) -> bool:
    """宽松匹配：允许同音字替换，只要听出「X维斯」就算唤醒。"""
    return any(k in text for k in ("贾维斯", "加维斯", "假维斯", "家维斯",
                                   "维斯", "维思", "为斯"))


def main() -> int:
    voiced, silent = load_all()
    print(f"样本：有人声 {len(voiced)} 片（应命中），环境噪声 {len(silent)} 片（不应命中）")
    print(f"对照：sherpa 3.3M KWS 召回 2/22 = 9%\n")

    for mname, mpath in MODELS:
        if not os.path.isdir(mpath):
            print(f"模型不存在，跳过：{mname}")
            continue
        for gname, grammar in (("宽松(含unk)", GRAMMAR), ("仅唤醒词", STRICT)):
            rec = make_rec(mpath, grammar)

            tp = 0
            lat = []
            for name, raw in voiced:
                text, at = run_streaming(rec, raw)
                if match(text):
                    tp += 1
                    if at > 0:
                        lat.append(at)
            fp = 0
            fp_names = []
            for name, raw in silent:
                text, _ = run_streaming(rec, raw)
                if match(text):
                    fp += 1
                    fp_names.append(f"{name} -> {text}")

            rec_rate = tp / len(voiced) * 100 if voiced else 0
            fp_rate = fp / len(silent) * 100 if silent else 0
            avg_lat = sum(lat) / len(lat) if lat else 0
            print(f"【{mname} / {gname}】")
            print(f"  召回 {tp}/{len(voiced)} = {rec_rate:.0f}%     "
                  f"误报 {fp}/{len(silent)} = {fp_rate:.0f}%     "
                  f"平均响应 {avg_lat:.2f}s")
            for n in fp_names[:5]:
                print(f"    误报: {n}")
            print()

    print("误报说明：无人声片若被判为唤醒，会导致贾维斯自己乱应声。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
