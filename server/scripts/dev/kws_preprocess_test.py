"""测试「前端预处理」能否把实时麦克风的音频拉回唤醒模型能认的分布。

背景：实时录音相对历史捕获，频谱呈"两头翘、中间凹"——
      0-200Hz 多 22%（低频隆隆），500-1000Hz 少 28%（元音第一共振峰被掏空），
      4-8kHz 多 211%（高频噪声）。
      这跟唤醒模型训练时的分布差太远，导致模型"自信地判错"。

做法：对同一批实时分片套用不同的预处理，看命中率从 4% 能拉到多少。
"""

from __future__ import annotations

import glob
import os
import re
import sys
import wave

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL = os.path.join(
    BASE, "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)
RATE = 16000


def load(path: str) -> np.ndarray:
    with wave.open(path, "rb") as f:
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def highpass(x: np.ndarray, fc: float = 80.0) -> np.ndarray:
    """一阶 IIR 高通，压掉低频隆隆声。"""
    a = 1.0 / (1.0 + 2 * np.pi * fc / RATE)
    y = np.empty_like(x)
    prev_x = 0.0
    prev_y = 0.0
    for i, v in enumerate(x):
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        y[i] = prev_y
    return y


def lowpass(x: np.ndarray, fc: float = 4000.0) -> np.ndarray:
    """一阶 IIR 低通，压掉高频噪声。"""
    a = 1.0 / (1.0 + 2 * np.pi * fc / RATE)
    y = np.empty_like(x)
    prev_y = 0.0
    for i, v in enumerate(x):
        prev_y = prev_y + a * (v - prev_y)
        y[i] = prev_y
    return y


def preemph(x: np.ndarray, k: float = 0.97) -> np.ndarray:
    """预加重：抬 6dB/oct，补回被掏空的中高频。"""
    y = np.empty_like(x)
    y[0] = x[0]
    y[1:] = x[1:] - k * x[:-1]
    return y


def norm_peak(x: np.ndarray, target: float = 0.9) -> np.ndarray:
    p = float(np.max(np.abs(x)))
    return x * (target / p) if p > 1e-6 else x


def norm_rms(x: np.ndarray, target: float = 0.09) -> np.ndarray:
    """按 RMS 归一化到历史捕获的量级（语音段 rms ≈ 0.06~0.09）。"""
    r = float(np.sqrt(np.mean(x**2)))
    g = target / r if r > 1e-9 else 1.0
    return np.clip(x * g, -1.0, 1.0)


PIPELINES = {
    "0 原始（基线）": lambda x: x,
    "1 高通80Hz": lambda x: highpass(x, 80),
    "2 预加重0.97": lambda x: preemph(x),
    "3 高通+预加重": lambda x: preemph(highpass(x, 80)),
    "4 带通80-4000": lambda x: lowpass(highpass(x, 80), 4000),
    "5 带通+预加重": lambda x: preemph(lowpass(highpass(x, 80), 4000)),
    "6 带通+预加重+峰值归一": lambda x: norm_peak(preemph(lowpass(highpass(x, 80), 4000))),
    "7 带通+预加重+RMS归一": lambda x: norm_rms(preemph(lowpass(highpass(x, 80), 4000))),
    "8 高通+RMS归一": lambda x: norm_rms(highpass(x, 80)),
}


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


def detect(sp, samples: list[float]) -> str:
    s = sp.create_stream()
    chunk = 512
    best = ""
    for i in range(0, len(samples), chunk):
        s.accept_waveform(RATE, samples[i : i + chunk])
        while sp.is_ready(s):
            sp.decode_stream(s)
            r = sp.get_result(s)
            kw = getattr(r, "keyword", None)
            if kw is None:
                kw = r if isinstance(r, str) else None
            if kw:
                best = kw
    s.accept_waveform(RATE, [0.0] * int(RATE * 0.4))
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

    # 只取有人声的分片（rms 明显高于底噪），避免静音片稀释统计
    voiced = []
    for f in files:
        a = load(f)
        r = float(np.sqrt(np.mean(a**2)))
        if r >= 0.004:
            voiced.append((f, a))
    print(f"总分片 {len(files)}，其中有人声的 {len(voiced)} 片（rms >= 0.004）\n")

    sp = make_spotter()
    print(f"{'预处理方案':<26s} {'命中':>8s} {'命中率':>8s}")
    print("-" * 46)

    best_name, best_hits = "", -1
    for name, fn in PIPELINES.items():
        hits = 0
        for _, a in voiced:
            y = fn(a.copy())
            y = np.clip(y, -1.0, 1.0)
            if detect(sp, y.astype(np.float32).tolist()):
                hits += 1
        rate = hits / len(voiced) * 100 if voiced else 0
        print(f"{name:<26s} {hits:>4d}/{len(voiced):<4d} {rate:>7.0f}%")
        if hits > best_hits:
            best_hits, best_name = hits, name

    print(f"\n最佳方案: {best_name} ({best_hits}/{len(voiced)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
