"""用 Vosk「受限语法识别」做唤醒词检测，与 sherpa KWS 做命中率对比。

为什么值得试：
  - sherpa 3.3M KWS 在实时录音上命中率只有 4%（22 片中 2 片）
  - Vosk 用的是完整 LVCSR 声学模型，而 grammar 把搜索空间锁死在唤醒词上，
    两者叠加的鲁棒性通常远高于端到端小模型
  - 之前放弃 Vosk 是因为**自由语音识别**不准（"你好 假 微丝"），
    但语法受限是另一回事——解码器只在唤醒词路径里搜，容错度完全不同

判据：在同样 22 片实时录音上，命中率能否大幅超过 4%。
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

MODELS = [
    # 大模型加载太慢，先只测 small

    ("small-cn-0.22", os.path.join(BASE, "models", "vosk", "vosk-model-small-cn-0.22")),
    # ("cn-0.22", os.path.join(BASE, "models", "vosk", "vosk-model-cn-0.22")),
]

# 语法：Vosk 的词表是按「字」组织的，整词会被忽略
# （实测会报 Ignoring word missing in vocabulary），所以必须拆成单字序列
KEYWORDS = ["你 好 贾 维 斯"]
GRAMMARS = {
    "仅唤醒词": json.dumps(KEYWORDS, ensure_ascii=False),
    "宽松(含unk)": json.dumps(KEYWORDS + ["[unk]"], ensure_ascii=False),
}


def load_voiced(pattern: str = r"^\d\d_"):
    d = os.path.join(BASE, "logs", "wakeword-live")
    files = sorted(glob.glob(os.path.join(d, "*.wav")))
    files = [f for f in files if re.match(pattern, os.path.basename(f))]
    out = []
    for f in files:
        with wave.open(f, "rb") as w:
            if w.getframerate() != RATE:
                continue
            raw = w.readframes(w.getnframes())
        # 只保留有人声的
        import math
        n = len(raw) // 2
        if n == 0:
            continue
        vals = list(wave.struct.unpack(f"<{n}h", raw[: n * 2]))
        rms = (sum(v * v for v in vals) / n) ** 0.5 / 32768.0
        if rms >= 0.004:
            out.append((os.path.basename(f), raw))
    return out


def run_vosk(model_path: str, grammar: str, raw: bytes, chunk: int = 1600) -> str:
    from vosk import KaldiRecognizer, Model

    rec = KaldiRecognizer(Model(model_path), RATE, grammar)
    rec.SetWords(True)
    text = ""
    for i in range(0, len(raw), chunk * 2):
        rec.AcceptWaveform(raw[i : i + chunk * 2])
    res = json.loads(rec.FinalResult())
    text = (res.get("text") or "").replace(" ", "")
    return text


def main() -> int:
    voiced = load_voiced()
    print(f"实时录音中有人声的分片：{len(voiced)} 片")
    print("对照：sherpa 3.3M KWS 命中 2/22 (9%)\n")

    for mname, mpath in MODELS:
        if not os.path.isdir(mpath):
            print(f"模型不存在，跳过：{mname}")
            continue
        for gname, grammar in GRAMMARS.items():
            hits, results = 0, []
            for name, raw in voiced:
                try:
                    text = run_vosk(mpath, grammar, raw)
                except Exception as e:
                    text = f"ERR:{e}"
                ok = ("贾维斯" in text or "加维斯" in text or "假维斯" in text or "家维斯" in text or "维斯" in text)
                if ok:
                    hits += 1
                    results.append(f"{name} -> {text}")
            rate = hits / len(voiced) * 100 if voiced else 0
            print(f"【{mname} / 语法={gname}】 命中 {hits}/{len(voiced)} = {rate:.0f}%")
            for r in results[:6]:
                print(f"    {r}")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
