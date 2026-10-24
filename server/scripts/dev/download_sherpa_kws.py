"""下载 sherpa-onnx 开源词表 KWS 中文模型。

走四级回源，因为 GitHub Releases 在国内网络下并不总是可达：

    1. 本地已存在且文件齐备        —— 直接跳过
    2. GitHub Releases 直连
    3. GitHub 代理（ghfast / gh-proxy / ghproxy）
    4. HuggingFace 镜像           —— server/.env 里已配 HF_ENDPOINT=hf-mirror.com，
                                    且 huggingface_hub 是 faster-whisper 的传递依赖，
                                    等于复用一条已经验证过通的下载通道

下载完成后**必须**校验四个模型文件齐备，缺任何一个都直接报错并指明应该放到哪个
目录 —— 不要让它拖到运行时才炸。

用法：
    python scripts/dev/download_sherpa_kws.py
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import tarfile
import time
import urllib.error
import urllib.request

MODEL_NAME = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
ARCHIVE = f"{MODEL_NAME}.tar.bz2"

RELEASE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{ARCHIVE}"
)

PROXIES = (
    "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{ARCHIVE}",
    "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{ARCHIVE}",
    # ghfast.top 在本机握手超时，留作最后一个兜底
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{ARCHIVE}",
)

# 四个缺一不可的文件：词表 + encoder/decoder/joiner 三个 int8 模型。
# 用 glob 而不是写死文件名：官方压缩包里的名字带训练配置后缀
# （encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx），换一份模型后缀就变了。
# 每组候选按「int8 优先」排序，与 app/wakeword.py 的 _sherpa_file 保持一致。
REQUIRED_FILES = (
    ("tokens.txt",),
    (
        "encoder-epoch-99-avg-1*.int8.onnx",
        "encoder-epoch-99-avg-1*.onnx",
        "encoder-*.int8.onnx",
        "encoder-*.onnx",
    ),
    (
        "decoder-epoch-99-avg-1*.int8.onnx",
        "decoder-epoch-99-avg-1*.onnx",
        "decoder-*.int8.onnx",
        "decoder-*.onnx",
    ),
    (
        "joiner-epoch-99-avg-1*.int8.onnx",
        "joiner-epoch-99-avg-1*.onnx",
        "joiner-*.int8.onnx",
        "joiner-*.onnx",
    ),
)

KEYWORDS_TXT = "keywords.txt"
KEYWORDS_RAW_TXT = "keywords_raw.txt"

# 与 app/wakeword.py 里的 WAKEWORD_MODEL_DIR 保持一致
DEFAULT_MODEL_ROOT = os.path.join("models", "wakeword")

# 默认唤醒词，写在**中文源**里；拼音 token 产物由 build_sherpa_keywords.py 编译。
# 刻意**不写** '#阈值'：行内 #x 是 per-keyword 阈值，会覆盖运行时的全局阈值
# 广播，导致诊断面板的灵敏度滑杆彻底失效。
DEFAULT_KEYWORDS_RAW = "你好贾维斯\n"


def server_root() -> str:
    """定位 server/ 目录（本脚本在 server/scripts/dev/ 下）。

    注意 __file__ 是**文件**路径，要先消掉文件名本身才能往上数：
        scripts/dev/download_sherpa_kws.py  →  up 1 = dev  →  up 2 = scripts
        →  up 3 = server
    少一级的话模型会被下到 server/scripts/models/ 里去，而 app/wakeword.py
    找的是 server/models/wakeword/，两边对不上。
    """
    here = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def model_dir(root: str | None = None) -> str:
    base = root or server_root()
    return os.path.join(base, DEFAULT_MODEL_ROOT, MODEL_NAME)


def check_complete(target: str) -> list[str]:
    """返回缺失的文件清单，空列表表示齐备。

    REQUIRED_FILES 的每一项是一组候选 glob，组内任一命中即算该文件齐备。
    """
    missing = []
    for patterns in REQUIRED_FILES:
        if not any(glob.glob(os.path.join(target, p)) for p in patterns):
            missing.append(patterns[0])
    return missing


def _content_length(url: str, timeout: int) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "jarvis-wakeword"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def _download(url: str, dest: str, timeout: int, max_attempts: int = 40) -> bool:
    """带断点续传的下载。

    本机实测下行只有 25~30 KB/s，31MB 的模型一次跑完几乎不可能 —— 中途
    断流是常态。如果每次重来，永远下不完。所以这里：

      1. 先 HEAD 拿到总长度；
      2. 每次尝试从 .part 的当前长度发 `Range: bytes=N-` 接着下；
      3. 断了就睡一秒重来，进度不丢。

    实测 GitHub 直连 / gh-proxy / ghproxy.net 都返回 Accept-Ranges: bytes，
    所以这条路是通的。
    """
    total = _content_length(url, timeout)
    if total:
        print(f"    总大小 {total // 1024 // 1024}MB，本机网速较慢，会自动续传")

    for attempt in range(1, max_attempts + 1):
        have = os.path.getsize(dest) if os.path.isfile(dest) else 0
        if total and have >= total:
            return True

        headers = {"User-Agent": "jarvis-wakeword"}
        if have and not total:
            have = 0  # 拿不到总长度时无法安全续传
        if have:
            headers["Range"] = f"bytes={have}-"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if have and getattr(resp, "status", 200) != 206:
                    # 服务端忽略了 Range，只能重头下
                    have = 0
                mode = "ab" if have else "wb"
                with open(dest, mode) as fh:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        if total:
                            pct = have * 100 // total
                            sys.stdout.write(
                                f"\r    已下载 {have // 1024}KB / {total // 1024}KB ({pct}%)"
                            )
                            sys.stdout.flush()
            sys.stdout.write("\n")
        except Exception as e:
            sys.stdout.write("\n")
            print(f"    第 {attempt} 次中断：{type(e).__name__}，已存 {have // 1024}KB，续传中")
            time.sleep(1)
            continue

        if not total or os.path.getsize(dest) >= total:
            return True

    return bool(total) and os.path.isfile(dest) and os.path.getsize(dest) >= total


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 sherpa-onnx 中文 KWS 模型")
    parser.add_argument("--timeout", type=int, default=120, help="单次下载超时秒数")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = parser.parse_args()

    target = model_dir()
    os.makedirs(os.path.dirname(target), exist_ok=True)

    missing = check_complete(target)
    if not missing and not args.force:
        print(f"模型已存在且完整：{target}")
        print("需要重新下载请加 --force")
    else:
        if missing:
            print(f"缺失文件：{', '.join(missing)}")

        archive = os.path.join(os.path.dirname(target), ARCHIVE)
        os.makedirs(os.path.dirname(archive), exist_ok=True)
        ok = False

        # 全部走 GitHub 发布资产。这里**不再尝试 HuggingFace**：实测
        # moeru-ai 下那个同名仓库只有 wasm 产物（preload.data 等），
        # 并没有我们需要的 tokens.txt 与 int8 onnx，试了也是白试。
        #
        # 顺序按本机实测可达性排：ghfast.top 握手超时，放最后。
        candidates = [("GitHub 直连", RELEASE_URL)] + [
            (f"代理 {i + 1}", url) for i, url in enumerate(PROXIES)
        ]
        for label, url in candidates:
            if ok:
                break
            try:
                print(f"[{label}] {url}")
                if not _download(url, archive, args.timeout):
                    print("    下载未完成，换下一个源")
                    continue
                print("    正在解压...")
                with tarfile.open(archive, "r:bz2") as tf:
                    tf.extractall(os.path.dirname(target))
                if not check_complete(target):
                    print("    成功")
                    ok = True
                    break
                print("    解出来了但文件不齐，换下一个源")
            except (
                urllib.error.URLError,
                TimeoutError,
                tarfile.TarError,
                OSError,
            ) as e:
                print(f"    失败：{e}")

        if os.path.isfile(archive) and ok:
            try:
                os.remove(archive)
            except OSError:
                pass

    missing = check_complete(target)
    if missing:
        print("\n下载失败：以下文件仍然缺失")
        for name in missing:
            print(f"  - {name}")
        print(f"\n请手动下载 {ARCHIVE} 并解压到：\n  {target}")
        return 1

    # 中文源 keywords_raw.txt 是「开放词表」的唯一入口，必须存在
    raw_path = os.path.join(target, KEYWORDS_RAW_TXT)
    if not os.path.isfile(raw_path):
        with open(raw_path, "w", encoding="utf-8") as fh:
            fh.write(DEFAULT_KEYWORDS_RAW)
        print(f"已生成中文唤醒词源：{raw_path}")
        print(f"内容：{DEFAULT_KEYWORDS_RAW.strip()}")

    # 再用同一套编译逻辑产出 keywords.txt，避免脚本与运行时两套拼音规则
    kw_path = os.path.join(target, KEYWORDS_TXT)
    if server_root() not in sys.path:
        sys.path.insert(0, server_root())
    try:
        from app.wakeword import _load_token_map, compile_keywords_text

        token_map = _load_token_map(os.path.join(target, "tokens.txt"))
        with open(raw_path, encoding="utf-8") as fh:
            words = [
                line.strip()
                for line in fh
                if line.strip() and not line.strip().startswith("#")
            ]
        with open(kw_path, "w", encoding="utf-8") as fh:
            fh.write(compile_keywords_text(words, token_map, 2.0))
        print(f"已编译拼音关键词：{kw_path}")
    except Exception as e:
        # 编译失败不该推翻下载成功的结论：提示用户单独跑编译脚本即可
        print(f"编译 keywords.txt 失败（可稍后跑 build_sherpa_keywords.py）：{e}")

    print(f"\n模型就绪：{target}")
    print("改唤醒词：编辑 keywords_raw.txt 后跑 scripts/dev/build_sherpa_keywords.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
