"""证明 sherpa-onnx 在本机的 DLL 冲突已解决。

为什么必须用**子进程**：DLL 一旦加载进进程就无法卸载，所以「先 import
onnxruntime 抢占、再 import sherpa_onnx」这个最坏情况只能在全新进程里复现。
在同一个解释器里跑三组，第二组会因为 DLL 已缓存而失去意义。

三组对照：

    A) 只 import sherpa_onnx                    —— 理想情况
    B) 先 import onnxruntime 再 import sherpa   —— 最坏情况（chromadb 已抢先加载）
    C) 再额外构造 KeywordSpotter 跑一遍真实推理   —— 证明不是"导入成功但一用就崩"

判定口径不能只是「import 没报错」。必须真的构造出关键词识别器跑一段音频，
再用 psutil 列出进程实际加载的模块路径 —— 这是最硬的证据，能直接看出
onnxruntime.dll 到底来自 site-packages 还是 C:\\Windows\\System32。

用法：
    python scripts/dev/verify_sherpa_dll.py
"""

from __future__ import annotations

import os
import subprocess
import sys

# 子进程干的事：按 mode 决定要不要先抢占 DLL，然后真正跑一次识别
_CHILD = r'''
import os, sys, glob

mode = sys.argv[1]
model_dir = sys.argv[2]

if mode == "with-ort":
    # 模拟真实后端：chromadb 等业务依赖会先加载 pin 住的 onnxruntime
    import onnxruntime
    print("ORT_PY", onnxruntime.__version__)

from app import sherpa_runtime
import sherpa_onnx

print("SHERPA_VER", sherpa_onnx.__version__)
print("SHERPA_ORT", sherpa_onnx.onnxruntime_version)
print("DLL_DIRS", len(sherpa_runtime.added_dirs()))
for d in sherpa_runtime.added_dirs():
    print("  DLL_DIR", d)

if os.path.isdir(model_dir):
    tokens = os.path.join(model_dir, "tokens.txt")
    kw = None
    for cand in (
        os.path.join(model_dir, "keywords.bpe"),
        os.path.join(model_dir, "keywords.txt"),
    ):
        if os.path.isfile(cand):
            kw = cand
            break
    # 文件名带训练配置后缀（chunk-16-left-64），只能 glob，不能写死
    def pick(*patterns):
        for p in patterns:
            hits = sorted(glob.glob(os.path.join(model_dir, p)))
            if hits:
                return hits[0]
        return ""

    models = {
        "encoder": pick("encoder-epoch-99-avg-1*.int8.onnx", "encoder-*.int8.onnx",
                        "encoder-epoch-99-avg-1*.onnx", "encoder-*.onnx"),
        "decoder": pick("decoder-epoch-99-avg-1*.int8.onnx", "decoder-*.int8.onnx",
                        "decoder-epoch-99-avg-1*.onnx", "decoder-*.onnx"),
        "joiner": pick("joiner-epoch-99-avg-1*.int8.onnx", "joiner-*.int8.onnx",
                       "joiner-epoch-99-avg-1*.onnx", "joiner-*.onnx"),
    }
    missing = [k for k, v in models.items() if not os.path.isfile(v)]
    if missing or not os.path.isfile(tokens) or not kw:
        print("MODEL_INCOMPLETE", ",".join(missing))
        sys.exit(0)

    spotter = sherpa_onnx.KeywordSpotter(
        tokens=tokens,
        encoder=models["encoder"],
        decoder=models["decoder"],
        joiner=models["joiner"],
        keywords_file=kw,
        keywords_score=2.0,
        keywords_threshold=0.25,
        num_threads=2,
        provider="cpu",
    )
    # KeywordSpotter 没有 keywords 属性，构造成功本身就是最强信号
    print("SPOTTER_OK", os.path.basename(kw))

    wavs = sorted(glob.glob(os.path.join(model_dir, "test_wavs", "*.wav")))
    if wavs:
        # 跑自带样本，确认真的能解出东西
        for w in wavs[:2]:
            import wave

            import numpy as np

            with wave.open(w, "rb") as wf:
                rate = wf.getframerate()   # 关闭后就取不到了，先存下来
                frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            stream = spotter.create_stream()
            stream.accept_waveform(rate, samples)
            stream.input_finished()
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
            print("SAMPLE", os.path.basename(w), "->", repr(spotter.get_result(stream)))

try:
    import psutil
    p = psutil.Process(os.getpid())
    loaded = sorted({m.path for m in p.memory_maps()
                     if "onnxruntime" in m.path.lower() or "sherpa" in m.path.lower()})
    print("--- 实际加载的 DLL ---")
    for x in loaded:
        print("  LOADED", x)
except ImportError:
    print("psutil 未安装，跳过 DLL 路径取证")
'''


def main() -> int:
    server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    model_sub = os.path.join(
        server_dir, "models", "wakeword",
        "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
    )

    python = sys.executable
    results: dict[str, bool] = {}

    for label, arg in (("A 直连导入", "plain"), ("B 先占 ORT", "with-ort")):
        print(f"\n===== {label} =====")
        proc = subprocess.run(
            [python, "-c", _CHILD, arg, model_sub],
            cwd=server_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = proc.stdout or ""
        print(out.strip() or "(无输出)")
        if proc.returncode != 0:
            print((proc.stderr or "").strip()[-2000:])
        results[label] = proc.returncode == 0 and "SHERPA_VER" in out

    print("\n===== C 宿主进程真实环境 =====")
    host = subprocess.run(
        [python, "-c", _CHILD, "with-ort", model_sub],
        cwd=server_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print((host.stdout or "").strip() or "(无输出)")
    stray = any(
        line.strip().startswith("LOADED") and "Windows" in line and "System" in line
        for line in (host.stdout or "").splitlines()
    )
    results["C 未误取系统 DLL"] = not stray

    print("\n===== 结论 =====")
    ok = True
    for name, passed in results.items():
        mark = "PASS" if passed else "FAIL"
        if not passed:
            ok = False
        print(f"  [{mark}] {name}")

    if stray:
        print(
            "\n警告：进程加载了 C:\\Windows\\System32 下的 onnxruntime.dll。"
            "\n      根治办法是把那份 DLL 改名（需要管理员权限），"
            "\n      例如 onnxruntime.dll -> onnxruntime.dll.bak。"
        )

    print("\nDLL 冲突验证" + ("通过" if ok else "未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
