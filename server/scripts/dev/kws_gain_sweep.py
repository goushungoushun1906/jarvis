"""对同一段音频做「增益扫描」，找出唤醒引擎实际需要的电平门限。

背景：实时录音（语音 rms ≈ 0.016）唤不醒，历史捕获（rms ≈ 0.06）能唤醒。
之前按 rms 归一化到 0.039 只中了 1/3，说明不是简单的线性增益问题，
所以这里把增益从 1x 一路扫到 12x，看命中率怎么变化。
"""

from __future__ import annotations

import array
import os
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL = os.path.join(
    BASE, "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)

SAMPLE_RATE = 16000


def load(path: str) -> array.array:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return a


def apply_gain(a: array.array, gain: float) -> array.array:
    return array.array(
        "h",
        [max(-32767, min(32767, int(x * gain))) for x in a],
    )


def stats(a: array.array) -> tuple[float, float]:
    rms = (sum((x / 32768.0) ** 2 for x in a) / max(1, len(a))) ** 0.5
    peak = max(abs(x) for x in a) / 32768.0
    return rms, peak


def make_spotter(boost: float, threshold: float):
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


def detect(sp, samples: list[float]) -> str:
    """按实时节奏分块喂入，返回命中的关键词。"""
    s = sp.create_stream()
    chunk = 512
    best = ""
    try:
        for i in range(0, len(samples), chunk):
            s.accept_waveform(SAMPLE_RATE, samples[i : i + chunk])
            while sp.is_ready(s):
                sp.decode_stream(s)
                r = sp.get_result(s)
                # 不同版本返回 str 或带 .keyword 的对象，两种都兼容
                kw = getattr(r, "keyword", None)
                if kw is None:
                    kw = r if isinstance(r, str) else None
                if kw:
                    best = kw
    finally:
        tail = samples[len(samples) - (len(samples) % chunk) :]
        if tail:
            s.accept_waveform(SAMPLE_RATE, tail)
        s.input_finished()
        while sp.is_ready(s):
            sp.decode_stream(s)
            r = sp.get_result(s)
            if r.keyword:
                best = r.keyword
    return best


def main() -> int:
    targets = [
        ("历史捕获（原本就命中）", "logs/wakeword/20261014-203116-你好贾维斯-100.wav"),
        ("实时录音 03", "logs/wakeword-live/03_rms0.0089_peak0.081.wav"),
        ("实时录音 06", "logs/wakeword-live/06_rms0.0112_peak0.142.wav"),
        ("实时录音 32", "logs/wakeword-live/32_rms0.0054_peak0.047.wav"),
        ("实时录音 44", "logs/wakeword-live/44_rms0.0079_peak0.072.wav"),
    ]

    gains = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]
    configs = [(2.0, 0.25), (10.0, 0.20)]

    print("引擎参数说明：boost=关键词得分加权, threshold=触发门限\n")

    for boost, threshold in configs:
        sp = make_spotter(boost, threshold)
        print(f"=== boost={boost}  threshold={threshold} ===")
        print(f"  {'样本':<22s} " + " ".join(f"{g:>5.0f}x" for g in gains))
        print("  " + "-" * (24 + 6 * len(gains)))

        for label, rel in targets:
            path = os.path.join(BASE, rel)
            if not os.path.isfile(path):
                print(f"  {label:<22s} (文件不存在)")
                continue
            a = load(path)
            base_rms, base_peak = stats(a)
            row = []
            for g in gains:
                b = apply_gain(a, g)
                rms, peak = stats(b)
                f = [x / 32768.0 for x in b]
                hit = detect(sp, f)
                row.append("  ✓  " if hit else f"{peak:.2f}x"[:5])
            print(f"  {label:<22s} " + " ".join(f"{c:>5s}" for c in row))
            print(f"  {'':<22s} 原始 rms={base_rms:.4f} peak={base_peak:.3f}")
        print()

    print("图例：✓ = 命中唤醒词；数字 = 未命中时的峰值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
