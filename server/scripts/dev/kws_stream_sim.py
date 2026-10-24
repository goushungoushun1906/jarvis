"""离线复现「实时流」喂法：按 512 采样分块喂给 KWS，不调 input_finished。

实时采集循环就是这么干的（wakeword.py 的 _capture_loop_inner）：
    accept_waveform(16000, 512 samples)
    while is_ready: decode
    get_result
如果这里不命中、而整段喂入命中，说明问题出在流式喂法/尾帧处理，而不是模型。
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
FRAME = 512


def read_wav(path):
    with wave.open(path, "rb") as f:
        rate = f.getframerate()
        raw = f.readframes(f.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    return [x / 32768.0 for x in a], rate


def make(boost=2.0, threshold=0.25):
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


def run_whole(sp, samples, rate):
    """整段喂 + input_finished（离线体检的写法）"""
    st = sp.create_stream()
    st.accept_waveform(rate, samples)
    st.accept_waveform(rate, [0.0] * int(rate * 0.66))
    st.input_finished()
    got = None
    while sp.is_ready(st):
        sp.decode_stream(st)
        r = sp.get_result(st)
        kw = getattr(r, "keyword", None) or (r if isinstance(r, str) else None)
        if kw:
            got = kw
    return got


def run_streamed(sp, samples, rate):
    """按 512 分块喂 + 每块后 while is_ready 解码（实时循环的写法）"""
    st = sp.create_stream()
    got = None
    for i in range(0, len(samples), FRAME):
        chunk = samples[i : i + FRAME]
        st.accept_waveform(rate, chunk)
        while sp.is_ready(st):
            sp.decode_stream(st)
        r = sp.get_result(st)
        if r:
            kw = getattr(r, "keyword", None) or (r if isinstance(r, str) else None)
            if kw:
                got = kw
    return got


def main():
    wavs = sorted(glob.glob(os.path.join("logs", "wakeword", "*.wav")))[-5:]
    if not wavs:
        print("没有样本")
        return
    sp1 = make()
    sp2 = make()
    print(f"{'文件':42s} {'整段喂':>10s} {'流式喂':>10s}")
    print("-" * 66)
    for w in wavs:
        samples, rate = read_wav(w)
        a = run_whole(sp1, samples, rate)
        b = run_streamed(sp2, samples, rate)
        print(
            f"{os.path.basename(w):42s} {str(a or '未命中'):>10s} {str(b or '未命中'):>10s}"
        )


if __name__ == "__main__":
    main()
