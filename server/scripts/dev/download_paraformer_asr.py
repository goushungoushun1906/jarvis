#!/usr/bin/env python3
"""下载 sherpa-onnx 的中文 Paraformer 语音识别模型，替换 Vosk 做指令转写。

为什么换：Vosk 的 vosk-model-small-cn 是老一代 Kaldi 模型，实测把
「你好贾维斯」识别成「你好 假 微丝」，长句错得更厉害；Paraformer 是
新一代非自回归模型，中文准确率高一档，int8 推理在 CPU 上也更快。

为什么选 small（78MB）而不是标准版（228MB）：本机下行只有 25~30KB/s，
标准版要下好几个小时。small 版足够跑通并验证收益，真觉得不够再跑
``--model int8`` 补下标准版（脚本会自动放到另一个目录，两者共存）。

用法::

    python scripts/dev/download_paraformer_asr.py            # small（默认）
    python scripts/dev/download_paraformer_asr.py --model int8  # 标准版

下载器直接复用 download_sherpa_kws.py 里那套带 Range 续传的实现：断流
是常态，没有续传就永远下不完。
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import time

# 复用同目录里已经过真机验证的探测函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from download_sherpa_kws import _content_length  # noqa: E402

# 两份候选：small 先跑通，int8 是质量更好的标准版
MODELS = {
    "small": "sherpa-onnx-paraformer-zh-small-2024-03-09",
    "int8": "sherpa-onnx-paraformer-zh-int8-2025-10-07",
}

# 非流式 Paraformer 只需要两个文件：模型本体 + 词表。
# 名字不能写死（不同版本的模型文件名不同），一律 glob，int8 优先。
REQUIRED_FILES = (
    ("tokens.txt",),
    ("model.int8.onnx", "model.onnx", "model_quant.onnx", "*.int8.onnx", "*.onnx"),
)

DEFAULT_MODEL_ROOT = os.path.join("models", "asr")

# 通道顺序是按本机实测速度排的：GitHub 直连实测 0 B/s（连不上），
# gh-proxy 约 26KB/s，ghproxy.net 约 6KB/s。直连仍保留在末尾兜底。
_MIRRORS = (
    "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/",
    "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/",
)

# 单连接只有 ~26KB/s，但**同一个文件开多段并发**能到 ~126KB/s（实测三段
# 各 30~54KB/s）——瓶颈在单连接限速而不是总带宽。所以默认切成 6 段并行。
SEGMENTS = 6

# 一段连接多久没有新数据就判死并重连。服务端会用"极慢但不间断"的方式吊着
# 连接，靠 socket 超时抓不到，必须看字节进展。
STALL_SECONDS = 45


def server_root() -> str:
    """定位 server/ 目录（本脚本在 server/scripts/dev/ 下）。"""
    here = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def model_dir(name: str, root: str | None = None) -> str:
    base = root or server_root()
    return os.path.join(base, DEFAULT_MODEL_ROOT, MODELS[name])


def check_complete(target: str) -> list[str]:
    """返回缺失的文件清单，空列表表示齐备。"""
    import glob as _glob

    missing = []
    for candidates in REQUIRED_FILES:
        if not any(_glob.glob(os.path.join(target, c)) for c in candidates):
            missing.append(candidates[0])
    return missing


def _download_segment(
    url: str, dest: str, start: int, end: int, timeout: int, max_attempts: int = 80
) -> bool:
    """下载文件的一段 [start, end]，支持按已存长度续传。

    每段独立一个 .partN 文件，各自记录进度：中断重跑时只补缺失的部分，
    已下完的段直接跳过。
    """
    import urllib.request

    seg_len = end - start + 1
    for attempt in range(1, max_attempts + 1):
        have = os.path.getsize(dest) if os.path.isfile(dest) else 0
        if have >= seg_len:
            return True
        headers = {"User-Agent": "jarvis-asr"}
        headers["Range"] = f"bytes={start + have}-{end}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if getattr(resp, "status", 200) != 206:
                    # 服务端忽略 Range：这段只能重头下
                    have = 0
                last_progress = time.time()
                with open(dest, "ab" if have else "wb") as fh:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        # 停滞检测：服务端可能用"极慢但不间断"的方式吊着连接，
                        # 光靠 socket 超时抓不到（它每 120 秒还是会挤几个字节）。
                        # 实测有一段就这么卡了 13 分钟。超过阈值就断了重连。
                        if time.time() - last_progress > STALL_SECONDS:
                            raise TimeoutError(f"超过 {STALL_SECONDS}s 没有新数据")
                        last_progress = time.time()
        except Exception as e:
            print(f"    段 {start // 1048576}MB 第 {attempt} 次中断：{type(e).__name__}，续传中")
            time.sleep(1)
            continue

        if os.path.getsize(dest) >= seg_len:
            return True
    return os.path.isfile(dest) and os.path.getsize(dest) >= seg_len


def download_parallel(url: str, dest: str, total: int, timeout: int, segments: int = SEGMENTS) -> bool:
    """多段并行下载后合并。

    断流是常态，所以每段都能独立续传；合并前逐段校验长度，缺一段就不合并，
    免得把残包当成品解压（tar.bz2 会在解压中途才报错，很难排查）。
    """
    import threading

    # 老版本单连接下载的半成品（就是 dest 本身）可以当成第 0 段的起点
    if os.path.isfile(dest) and not os.path.isfile(f"{dest}.part0"):
        size = os.path.getsize(dest)
        if 0 < size < total:
            os.rename(dest, f"{dest}.part0")

    bounds = []
    step = total // segments
    for i in range(segments):
        start = i * step
        end = total - 1 if i == segments - 1 else (start + step - 1)
        bounds.append((start, end))

    results: list[bool] = [False] * segments

    def worker(idx: int, start: int, end: int) -> None:
        results[idx] = _download_segment(url, f"{dest}.part{idx}", start, end, timeout)

    threads = [
        threading.Thread(target=worker, args=(i, start, end), daemon=True)
        for i, (start, end) in enumerate(bounds)
    ]
    for t in threads:
        t.start()

    # 进度提示：不逐段打印，避免刷屏
    while any(t.is_alive() for t in threads):
        done = sum(os.path.getsize(f"{dest}.part{i}") for i in range(segments) if os.path.isfile(f"{dest}.part{i}"))
        pct = done * 100 // total if total else 0
        sys.stdout.write(f"\r    已下载 {done // 1048576}MB / {total // 1048576}MB ({pct}%)   ")
        sys.stdout.flush()
        time.sleep(2)
    for t in threads:
        t.join()
    sys.stdout.write("\n")

    if not all(results):
        print("部分分段未完成，重跑本脚本会续传（已下完的段不会重下）")
        return False

    with open(dest, "wb") as out:
        for i in range(segments):
            with open(f"{dest}.part{i}", "rb") as fh:
                while True:
                    chunk = fh.read(1048576)
                    if not chunk:
                        break
                    out.write(chunk)
            os.unlink(f"{dest}.part{i}")

    if os.path.getsize(dest) != total:
        print(f"合并后大小不符：{os.path.getsize(dest)} / {total}")
        return False
    return True


def extract(archive: str, target: str) -> None:
    os.makedirs(target, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar.getmembers():
            # 压缩包里带一层顶层目录，剥掉；同时防路径穿越
            parts = member.name.split("/")
            if len(parts) > 1:
                member.name = "/".join(parts[1:])
            if not member.name or member.name.startswith(".."):
                continue
            tar.extract(member, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 sherpa-onnx 中文 Paraformer ASR 模型")
    parser.add_argument(
        "--model", choices=sorted(MODELS), default="small",
        help="small=78MB（默认，下载快）；int8=228MB（质量更好）",
    )
    parser.add_argument("--timeout", type=int, default=120, help="单次下载超时秒数")
    parser.add_argument(
        "--segments", type=int, default=SEGMENTS,
        help="并行分段数（默认 6；单连接限速时加大能明显提速）",
    )
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = parser.parse_args()

    name = MODELS[args.model]
    archive = f"{name}.tar.bz2"
    target = model_dir(args.model)

    if not args.force:
        missing = check_complete(target)
        if not missing:
            print(f"模型已就绪：{target}")
            return 0
        print(f"缺失文件：{missing}")

    os.makedirs(target, exist_ok=True)
    dest = os.path.join(target, archive)

    urls = [base + archive for base in _MIRRORS]
    ok = False
    chosen = urls[0]
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url.split('/')[2]}")
        total = _content_length(url, args.timeout)
        if not total:
            print("    拿不到文件大小，换下一个通道")
            continue
        print(f"    总大小 {total // 1048576}MB")
        if download_parallel(url, dest, total, args.timeout, segments=args.segments):
            ok = True
            chosen = url
            break
        print("    该通道未完成，换下一个")
        time.sleep(1)

    if not ok:
        print("下载未完成：重跑本脚本会续传（已下完的分段不会重下）。")
        return 1

    # 体积校验：下到一半的残包解压会报"数据错误"，先把话说清楚
    expected = _content_length(chosen, args.timeout)
    actual = os.path.getsize(dest)
    if expected and actual != expected:
        print(f"压缩包不完整：{actual} / {expected} 字节，重跑本脚本续传")
        return 1

    print("解压中…")
    extract(dest, target)
    os.unlink(dest)

    missing = check_complete(target)
    if missing:
        print(f"解压后仍缺文件：{missing}")
        return 1

    print(f"\n模型就绪：{target}")
    for c in REQUIRED_FILES[1]:
        import glob as _glob
        hits = _glob.glob(os.path.join(target, c))
        if hits:
            mb = os.path.getsize(hits[0]) / 1e6
            print(f"  模型文件 {os.path.basename(hits[0])} ({mb:.0f}MB)")
            break
    print("提示：app/stt.py 会自动发现它；也可用 JARVIS_ASR_MODEL_DIR 指定目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
