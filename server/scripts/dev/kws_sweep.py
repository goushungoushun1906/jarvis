"""唤醒参数扫描：同一批录音、不同 boost / threshold 组合，看命中率。

用法：
    .venv\\Scripts\\python.exe scripts/dev/kws_sweep.py [音频目录]

默认扫 logs/wakeword 下最近 5 条捕获。
"""

import os
import sys
import glob
import wave
import array

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sherpa_onnx  # noqa: E402

MODEL_DIR = os.path.join(
    "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)
KW_FILE = os.path.join(MODEL_DIR, "keywords.txt")


def read_wav(path):
    with wave.open(path, "rb") as f:
        rate = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return [x / 32768.0 for x in a], rate


def make(boost, threshold):
    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(MODEL_DIR, "tokens.txt"),
        encoder=os.path.join(MODEL_DIR, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=os.path.join(MODEL_DIR, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=os.path.join(MODEL_DIR, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=KW_FILE,
        num_threads=2,
        provider="cpu",
        keywords_score=boost,
        keywords_threshold=threshold,
    )


def detect(sp, samples, rate):
    st = sp.create_stream()
    st.accept_waveform(rate, samples)
    st.accept_waveform(rate, [0.0] * int(rate * 0.66))
    st.input_finished()
    while sp.is_ready(st):
        sp.decode_stream(st)
        r = sp.get_result(st)
        kw = getattr(r, "keyword", None)
        if kw is None:
            kw = r if isinstance(r, str) else None
        if kw:
            return kw
    return None


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join("logs", "wakeword")
    wavs = sorted(glob.glob(os.path.join(d, "*.wav")))[-5:]
    if not wavs:
        print("没有找到 wav:", d)
        return
    print(f"样本 {len(wavs)} 条，来自 {d}\n")

    combos = [
        (2.0, 0.25),   # 当前 App 默认值
        (2.0, 0.20),
        (5.0, 0.25),
        (10.0, 0.25),
        (10.0, 0.20),  # 体检脚本用的值（已验证能命中）
        (20.0, 0.25),
    ]
    print(f"{'boost/thr':>14s} | 命中数 | 明细")
    print("-" * 60)
    for boost, thr in combos:
        try:
            sp = make(boost, thr)
        except Exception as exc:  # noqa: BLE001
            print(f"{boost:6.1f}/{thr:.2f} | 构造失败: {exc}")
            continue
        hits = 0
        detail = []
        for w in wavs:
            samples, rate = read_wav(w)
            got = detect(sp, samples, rate)
            hits += 1 if got else 0
            detail.append("Y" if got else ".")
        print(f"{boost:6.1f}/{thr:.2f} |  {hits}/{len(wavs)}   | {''.join(detail)}")


if __name__ == "__main__":
    main()
