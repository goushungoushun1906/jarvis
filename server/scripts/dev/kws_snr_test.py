"""信噪比敏感度测试：给历史命中样本掺噪声，找出唤醒引擎能容忍的 SNR 下限。

背景：2026-10-24 实喊验收时，麦克风链路正常（ASR 能准确识别「你好贾维斯」），
但 KWS 大多不命中。对比发现当天语音 rms≈0.009、底噪 rms≈0.0011（SNR≈18dB），
而 10-14 能命中时 rms≈0.039（SNR≈32dB）。本脚本验证「SNR 下降导致漏唤醒」这一假设。

用法（server 目录）：
    .venv\\Scripts\\python.exe scripts/dev/kws_snr_test.py
"""

import os
import sys
import wave
import array
import math
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sherpa_onnx  # noqa: E402

MODEL_DIR = os.path.join(
    "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)
KW_FILE = os.path.join(MODEL_DIR, "keywords.txt")
NOISE_SRC = os.path.join("logs", "wakeword-live")  # 用真实环境底噪，比合成白噪声更贴近实际


def load_wav(path):
    with wave.open(path, "rb") as f:
        rate = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return [x / 32768.0 for x in a], rate


def rms(xs):
    return (sum(x * x for x in xs) / max(1, len(xs))) ** 0.5


def make_spotter():
    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(MODEL_DIR, "tokens.txt"),
        encoder=os.path.join(MODEL_DIR, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=os.path.join(MODEL_DIR, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=os.path.join(MODEL_DIR, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=KW_FILE,
        num_threads=2,
        provider="cpu",
        keywords_score=2.0,
        keywords_threshold=0.25,
    )


def detect(sp, samples, rate):
    st = sp.create_stream()
    st.accept_waveform(rate, samples)
    st.accept_waveform(rate, [0.0] * int(rate * 0.5))
    st.input_finished()
    got = None
    while sp.is_ready(st):
        sp.decode_stream(st)
        r = sp.get_result(st)
        kw = getattr(r, "keyword", None) or (r if isinstance(r, str) else None)
        if kw:
            got = kw
    return got


def main():
    # 取历史命中样本
    hist = sorted(
        p for p in os.listdir("logs/wakeword")
        if p.endswith(".wav") and "你好贾维斯" in p
    )
    if not hist:
        print("找不到历史捕获样本")
        return
    src = os.path.join("logs/wakeword", hist[-1])
    speech, rate = load_wav(src)
    s_rms = rms(speech)

    # 真实环境底噪：取实时录音里最安静的几片拼起来
    noise = []
    if os.path.isdir(NOISE_SRC):
        quiet = sorted(
            p for p in os.listdir(NOISE_SRC)
            if p.endswith(".wav") and p.startswith(("17_", "19_", "21_", "23_", "28_"))
        )
        for p in quiet:
            n, _ = load_wav(os.path.join(NOISE_SRC, p))
            noise.extend(n)
    if not noise:
        print("找不到真实底噪样本，退化为白噪声")
        random.seed(0)
        noise = [random.uniform(-1, 1) * 0.001 for _ in range(rate * 3)]

    random.seed(42)
    print(f"样本: {hist[-1]}  语音 rms={s_rms:.4f}  底噪 rms={rms(noise):.5f}\n")
    print(f"{'目标SNR':>8s} {'实际SNR':>8s} {'结果':>12s}")
    print("-" * 34)

    sp = make_spotter()
    for target_db in (99, 30, 24, 20, 18, 15, 12, 8):
        if target_db == 99:
            test = list(speech)
            label = "原始"
        else:
            # 按目标 SNR 计算噪声增益
            g = s_rms / (rms(noise) * (10 ** (target_db / 20.0)))
            n = list(noise)
            random.shuffle(n)
            # 底噪长度不够就循环
            while len(n) < len(speech):
                n.extend(noise)
            test = [
                speech[i] + n[i] * g for i in range(len(speech))
            ]
            label = f"{target_db}dB"
        noise_only = [test[i] - speech[i] for i in range(len(speech))]
        actual = 20 * math.log10(s_rms / max(1e-9, rms(noise_only)))
        hit = detect(sp, test, rate)
        print(f"{label:>8s} {actual:>7.1f}dB {str(hit or '未命中'):>12s}")


if __name__ == "__main__":
    main()
