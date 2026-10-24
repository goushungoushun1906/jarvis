"""把中文唤醒词编译成 sherpa-onnx 能读的 keywords.txt。

为什么要这个脚本：sherpa 的中文 KWS 模型是**拼音建模**的，喂给 KeywordSpotter
的关键词文件里必须是拼音 token（``n ǐ h ǎo j iǎ w éi s ī``），不能直接写汉字。
所以唤醒词有两份文件：

    keywords_raw.txt   一行一个中文词，给人看、给人改   ← 唯一真源，入库
    keywords.txt       编译出的拼音 token 行，给 sherpa 读

改唤醒词的完整动作就是：改 keywords_raw.txt，跑一次本脚本。运行时也会在检测到
raw 比 txt 新时自动编译，但这个脚本能立刻看到编译结果和 OOV 报错，不用等启动。

用法：
    python scripts/dev/build_sherpa_keywords.py                  # 按 raw 重新编译
    python scripts/dev/build_sherpa_keywords.py --check          # 只预览不落盘
    python scripts/dev/build_sherpa_keywords.py --words 你好贾维斯,小爱同学
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.wakeword import (  # noqa: E402
    _discover_sherpa_model_dir,
    _load_token_map,
    compile_keywords_text,
    pinyin_tokens,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="编译 sherpa 中文唤醒词")
    parser.add_argument("--model-dir", default="", help="模型目录，默认自动发现")
    parser.add_argument("--words", default="", help="逗号分隔的中文词，覆盖 raw 文件")
    parser.add_argument("--boost", type=float, default=2.0, help="关键词得分增益")
    parser.add_argument("--check", action="store_true", help="只打印不写文件")
    args = parser.parse_args()

    model_dir = args.model_dir.strip() or _discover_sherpa_model_dir()
    if not model_dir:
        print("未找到 sherpa 模型目录，先跑 scripts/dev/download_sherpa_kws.py")
        return 1

    tokens_path = os.path.join(model_dir, "tokens.txt")
    if not os.path.isfile(tokens_path):
        print("缺少 tokens.txt：%s" % tokens_path)
        return 1
    token_map = _load_token_map(tokens_path)
    if not token_map:
        print("tokens.txt 解析为空：%s" % tokens_path)
        return 1

    if args.words.strip():
        words = [w.strip() for w in args.words.split(",") if w.strip()]
        raw_path = ""
    else:
        raw_path = os.path.join(model_dir, "keywords_raw.txt")
        if not os.path.isfile(raw_path):
            print("缺少中文词源：%s（可用 --words 指定）" % raw_path)
            return 1
        with open(raw_path, "r", encoding="utf-8") as f:
            words = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    if not words:
        print("没有任何唤醒词")
        return 1

    try:
        text = compile_keywords_text(words, token_map, args.boost)
    except ImportError:
        print("缺少 pypinyin，装一下：pip install pypinyin （仅改词时需要）")
        return 1
    except ValueError as e:
        print("编译失败：%s" % e)
        return 1

    print("模型目录：%s" % model_dir)
    if raw_path:
        print("中文词源：%s" % raw_path)
    for word in words:
        print("  %s → %s" % (word, " ".join(pinyin_tokens(word, token_map))))
    print("--- keywords.txt 内容 ---")
    print(text.rstrip())

    if args.check:
        print("\n（--check 模式，未写入）")
        return 0

    out = os.path.join(model_dir, "keywords.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n已写入：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
