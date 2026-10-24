"""对一段时间窗内的全部实时分片跑唤醒引擎，统计命中率并分析命中的分片有什么共性。

用法：python scripts/dev/kws_batch.py [目录] [文件名正则]
"""

from __future__ import annotations

import array
import glob
import os
import re
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL = os.path.join(
    BASE, "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)


def load(path: str) -> array.array:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return a


def stats(a: array.array) -> tuple[float, float]:
    rms = (sum((x / 32768.0) ** 2 for x in a) / max(1, len(a))) ** 0.5
    peak = max(abs(x) for x in a) / 32768.0
    return rms, peak


def make_spotter(boost: float = 2.0, threshold: float = 0.25):
    import sherpa_onnx

    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(MODEL, "tokens.txt"),
        encoder=os.path.join(MODEL, "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
        decoder=os.path.join(MODEL, "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"),
        joiner=os.path.join(MODEL, "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"),
        keywords_file=os.path.join(MODEL, "keywords.txt"),
        keywords_score=boost,
        keywords_threshold=threshold,
        num_threads=2,
        provider="cpu",
    )


def detect(sp, samples: list[float], rate: int = 16000) -> str:
    s = sp.create_stream()
    chunk = 512
    best = ""
    for i in range(0, len(samples), chunk):
        s.accept_waveform(rate, samples[i : i + chunk])
        while sp.is_ready(s):
            sp.decode_stream(s)
            r = sp.get_result(s)
            kw = getattr(r, "keyword", None)
            if kw is None:
                kw = r if isinstance(r, str) else None
            if kw:
                best = kw
    tail = [0.0] * int(rate * 0.4)
    s.accept_waveform(rate, tail)
    s.input_finished()
    while sp.is_ready(s):
        sp.decode_stream(s)
        r = sp.get_result(s)
        kw = getattr(r, "keyword", None)
        if kw is None:
            kw = r if isinstance(r, str) else None
        if kw:
            best = kw
    return best


def main() -> int:
    d = sys.argv[1] if len(sys.argv) > 1 else "logs/wakeword-live"
    pattern = sys.argv[2] if len(sys.argv) > 2 else r"^\d\d_"
    d = os.path.join(BASE, d) if not os.path.isabs(d) else d

    files = sorted(glob.glob(os.path.join(d, "*.wav")))
    files = [f for f in files if re.match(pattern, os.path.basename(f))]
    if not files:
        print("没有匹配的分片")
        return 1

    sp = make_spotter()
    hits, misses = [], []
    for f in files:
        a = load(f)
        rms, peak = stats(a)
        hit = detect(sp, [x / 32768.0 for x in a])
        name = os.path.basename(f)
        if hit:
            hits.append((name, rms, peak))
        else:
            misses.append((name, rms, peak))

    print(f"目录: {d}")
    print(f"分片数: {len(files)}   命中 {len(hits)}   未命中 {len(misses)}")
    print(f"命中率: {len(hits)/len(files)*100:.0f}%\n")

    if hits:
        print("【命中】")
        for n, r, p in hits:
            print(f"  {n:<42s} rms={r:.4f} peak={p:.3f}")
        ha = sum(r for _, r, _ in hits) / len(hits)
        hp = sum(p for _, _, p in hits) / len(hits)
        print(f"  → 平均 rms={ha:.4f}  peak={hp:.3f}\n")

    # 未命中的按能量排前几名
    misses.sort(key=lambda x: -x[1])
    print("【未命中 · 能量最高的 8 片】")
    for n, r, p in misses[:8]:
        print(f"  {n:<42s} rms={r:.4f} peak={p:.3f}")
    if misses:
        ma = sum(r for _, r, _ in misses) / len(misses)
        print(f"  → 全部未命中平均 rms={ma:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
