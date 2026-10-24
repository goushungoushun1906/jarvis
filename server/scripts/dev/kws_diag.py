"""唤醒引擎体检：把「模型 / 关键词文件 / 音频」三个环节逐个隔离验证。

用法（在 server 目录下）：
    .venv\\Scripts\\python.exe scripts/dev/kws_diag.py

会依次跑：
  A. 官方 test_keywords.txt + 官方 test_wavs    -> 验证引擎 & DLL 是否正常
  B. 我方 keywords.txt     + 官方 test_wavs    -> 验证关键词文件格式是否被正确解析
  C. 我方 keywords.txt     + logs/wakeword/*.wav -> 验证历史捕获能否复现命中
  D. （可选）我方 keywords.txt + 指定 wav        -> python scripts/dev/kws_diag.py xxx.wav
"""

import os
import sys
import glob
import wave

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import sherpa_onnx  # noqa: E402

MODEL_DIR = os.path.join(
    "models", "wakeword", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)
CAPTURE_DIR = os.path.join("logs", "wakeword")


def read_wav(path):
    """返回 (samples_float32_list, sample_rate)"""
    with wave.open(path, "rb") as f:
        assert f.getnchannels() == 1, f"需要单声道: {path}"
        rate = f.getframerate()
        raw = f.readframes(f.getnframes())
    import array

    a = array.array("h")
    a.frombytes(raw)
    return [x / 32768.0 for x in a], rate


def make_spotter(keywords_file):
    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(MODEL_DIR, "tokens.txt"),
        encoder=os.path.join(MODEL_DIR, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=os.path.join(MODEL_DIR, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=os.path.join(MODEL_DIR, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        keywords_file=keywords_file,
        num_threads=2,
        provider="cpu",
        max_active_paths=4,
        keywords_score=10.0,
        keywords_threshold=0.20,
    )


def run(spotter, wav_path, label):
    samples, rate = read_wav(wav_path)
    stream = spotter.create_stream()
    stream.accept_waveform(rate, samples)
    # 尾部补一点静音，让最后几个 chunk 被 flush 出来
    tail = [0.0] * int(rate * 0.5)
    stream.accept_waveform(rate, tail)
    stream.input_finished()

    best = None
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        r = spotter.get_result(stream)
        # 不同版本返回 str 或带 .keyword 的对象，两种都兼容
        kw = getattr(r, "keyword", None)
        if kw is None:
            kw = r if isinstance(r, str) else None
        if kw:
            best = kw
    return best


def main():
    print("sherpa_onnx:", sherpa_onnx.__file__)
    print("model dir  :", os.path.abspath(MODEL_DIR))
    print()

    official_kw = os.path.join(MODEL_DIR, "test_wavs", "test_keywords.txt")
    mine_kw = os.path.join(MODEL_DIR, "keywords.txt")

    # ---------- A + B：官方测试音频 ----------
    test_wavs = sorted(glob.glob(os.path.join(MODEL_DIR, "test_wavs", "*.wav")))
    if test_wavs:
        print("=== A. 官方关键词 + 官方测试音频（验证引擎本身） ===")
        sp = make_spotter(official_kw)
        ok = 0
        for w in test_wavs:
            hit = run(sp, w, "official")
            print(f"  {os.path.basename(w):8s} -> {hit or '(未命中)'}")
            ok += 1 if hit else 0
        print(f"  命中 {ok}/{len(test_wavs)}")
        print()

        print("=== B. 我方关键词 + 官方测试音频（应为未命中，纯粹测文件能否解析） ===")
        try:
            sp2 = make_spotter(mine_kw)
            print(f"  keywords.txt 解析成功，内部关键词数: {len(getattr(sp2, 'keywords', []) or [])}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] keywords.txt 解析异常: {exc}")
            sp2 = None
        print()

    # ---------- C：历史捕获 ----------
    caps = sorted(glob.glob(os.path.join(CAPTURE_DIR, "*.wav")))[-5:]
    if caps:
        print("=== C. 我方关键词 + 历史捕获（验证这些录音能否复现命中） ===")
        sp3 = make_spotter(mine_kw)
        for w in caps:
            hit = run(sp3, w, "capture")
            print(f"  {os.path.basename(w):40s} -> {hit or '(未命中)'}")
        print()

    # ---------- D：指定音频 ----------
    for extra in sys.argv[1:]:
        if os.path.isfile(extra):
            print(f"=== D. 我方关键词 + {extra} ===")
            sp4 = make_spotter(mine_kw)
            print("  ->", run(sp4, extra, "extra") or "(未命中)")


if __name__ == "__main__":
    main()
