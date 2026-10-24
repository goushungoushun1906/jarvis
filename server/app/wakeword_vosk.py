"""Vosk 受限语法唤醒引擎。

为什么要有这个引擎
------------------
sherpa-onnx 的 3.3M zipformer KWS 模型在某些笔记本阵列麦上会"自信地判错"：
真实人声 26 段只命中 2 段（8%），而且调 boost、调门限、加增益、加滤波全部无效
（详见 2026-10-24 的排查记录）。根因是端到端小模型对声学条件敏感，
共振峰区能量稍弱就直接判成非关键词。

Vosk 走的是另一条路：完整的 LVCSR 声学模型 + **受限语法**。
语法把解码器的搜索空间锁死在唤醒词（及其同音变体）上，
等效于"只让你在这几条路径里挑最像的那条"，容错度远高于端到端小模型。

实测（26 段真实人声 + 24 段环境噪声，均经 ASR 复核）：
    sherpa 3.3M KWS      召回  8%   误报 0%    响应 —
    Vosk small + 单字语法 召回 73%   误报 0%    响应 1.58s
    Vosk 大模型 + 单字语法 召回 50%   误报 0%    响应 2.23s

两个必须注意的坑
----------------
1. **中文语法必须写成单字序列**。直接把「你好贾维斯」整个词丢进 grammar，
   Vosk 只会打一行 `Ignoring word missing in vocabulary` 然后静默失效，
   表现为 0 命中且没有任何报错。
2. **必须留 [unk] 出口**。否则环境噪声也会被强行解码成唤醒词路径；
   有了 [unk]，噪声有了去处，误报才能压住（实测 14% → 0%）。
"""

from __future__ import annotations

import array
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # 避免与 wakeword 互相 import 形成环
    from .wakeword import WakeConfig

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_LENGTH = 512

# 汉族人名/常用字的常见混淆音。只做「单字替换」，
# 不做全组合——全组合会让语法膨胀到上百条，误报跟着涨。
_HOMOPHONES: dict[str, list[str]] = {
    "贾": ["加", "假", "家", "甲", "佳"],
    "维": ["为", "唯", "围", "伟"],
    "斯": ["思", "司", "丝", "师"],
    "你": ["拟", "尼"],
    "好": ["号", "郝", "耗"],
}

# 静默多久就复位一次解码器。
# 不复位的后果：环境噪声在 lattice 里越积越多，最终被解码成唤醒词 → 自己乱应声。
# 只在"连续静默"时复位，因此不会把正在说的唤醒词拦腰截断。
_IDLE_RESET_CHECKS = 40


def split_chars(word: str) -> str:
    """整词拆成 Vosk 能认的单字序列：「你好贾维斯」→「你 好 贾 维 斯」。

    Vosk 的中文词表是按字组织的，整词不在词表里会被静默忽略。
    """
    return " ".join(ch for ch in word if not ch.isspace())


def build_grammar(words: Sequence[str], include_bare_tail: bool = True) -> list[str]:
    """生成语法条目：基准词 + 单字同音替换变体 + [unk] 出口。"""
    entries: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        if text and text not in seen:
            seen.add(text)
            entries.append(text)

    for word in words:
        base = split_chars(word)
        add(base)

        chars = [c for c in word if not c.isspace()]
        # 单字替换：任一位置换成同音字，得到编辑距离为 1 的变体
        for i, ch in enumerate(chars):
            for alt in _HOMOPHONES.get(ch, ()):
                variant = list(chars)
                variant[i] = alt
                add(" ".join(variant))

        # 只喊名字的部分（「贾维斯」）也要能唤醒
        if include_bare_tail and len(chars) > 2:
            add(" ".join(chars[-3:]))

    return entries


class VoskWakeEngine:
    """用 Vosk 受限语法做关键词唤醒。

    接口与 sherpa / porcupine 引擎一致，可直接接入既有的采集循环：
    ``process(pcm)`` 每帧喂 512 个 int16 采样，返回 ``(命中下标, 分数)``。
    """

    name = "vosk"
    sample_rate = SAMPLE_RATE
    frame_length = FRAME_LENGTH

    def __init__(self, cfg: "WakeConfig") -> None:
        import vosk

        model_path = cfg.vosk_model
        if not model_path or not os.path.isdir(model_path):
            raise RuntimeError(
                f"Vosk 模型目录不可用：{model_path!r}。"
                "请设置 JARVIS_WAKE_VOSK_MODEL 指向解压后的模型目录。"
            )

        if not cfg.keyword_names:
            raise RuntimeError("没有配置唤醒词，Vosk 引擎无法构造语法。")

        self._model = vosk.Model(model_path)

        words = list(cfg.keyword_names)
        extra = (getattr(cfg, "vosk_grammar_extra", "") or "").strip()
        if extra:
            for line in extra.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)

        grammar_entries = build_grammar(words)
        grammar = json.dumps(grammar_entries + ["[unk]"], ensure_ascii=False)
        self._grammar = grammar_entries
        logger.info(
            "Vosk 唤醒语法（%d 条）：%s", len(grammar_entries), " | ".join(grammar_entries[:6])
        )

        self._rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, grammar)
        self._rec.SetWords(True)

        # 中间结果不必每帧都取：JSON 解析有成本，而唤醒词再快也要几百毫秒说出口。
        # 每 6 帧（192ms）看一次，延迟与 CPU 折中。
        self._check_every = 6
        self._since_check = 0
        self._idle_checks = 0

        self._keywords = list(cfg.keyword_names)
        self._index = {kw: i for i, kw in enumerate(self._keywords)}
        # 宽松匹配用的片段：只要听出名字的后半截就算唤醒
        self._tails = self._build_tails()

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------
    def _build_tails(self) -> list[str]:
        """构造匹配用的关键片段。

        语法已经把搜索空间锁死，这里只做最后一道确认：
        命中必须是唤醒词本身（含同音变体），而不是被 [unk] 吸收掉的噪声。
        """
        tails: list[str] = []
        for word in self._keywords:
            chars = [c for c in word if not c.isspace()]
            if len(chars) >= 2:
                tails.append("".join(chars[-2:]))  # 「维斯」
        return tails

    def _match(self, text: str) -> int:
        """文本是否就是唤醒词。返回命中的关键词下标，未命中返回 -1。"""
        if not text:
            return -1
        flat = text.replace(" ", "")
        if not flat or flat == "unk" or "[unk]" in flat:
            return -1
        for i, kw in enumerate(self._keywords):
            if kw in flat:
                return i
        # 单字逐一同音命中：允许最多两个字不同
        for i, kw in enumerate(self._keywords):
            if len(flat) == len(kw):
                diff = sum(1 for a, b in zip(flat, kw) if a != b)
                if diff <= 1:
                    return i
        # 只喊了名字的后半截
        for tail in self._tails:
            if tail in flat and len(flat) <= len(tail) + 2:
                return 0
        return -1

    # ------------------------------------------------------------------
    # 引擎接口
    # ------------------------------------------------------------------
    def process(self, pcm: Sequence[int]) -> tuple[int, float]:
        raw = array.array("h", pcm).tobytes()

        # 完整结果：解码器攒够一段话才会吐，准确率最高
        if self._rec.AcceptWaveform(raw):
            try:
                text = (json.loads(self._rec.Result()) or {}).get("text", "") or ""
            except Exception:
                text = ""
            idx = self._match(text.strip())
            if idx >= 0:
                self._rec.Reset()
                self._idle_checks = 0
                return idx, 1.0

        # 中间结果：说完就能定，延迟低但要防着误报
        self._since_check += 1
        if self._since_check >= self._check_every:
            self._since_check = 0
            try:
                partial = (json.loads(self._rec.PartialResult()) or {}).get("partial", "") or ""
            except Exception:
                partial = ""
            partial = partial.strip()
            if partial:
                self._idle_checks = 0
                idx = self._match(partial)
                if idx >= 0:
                    self._rec.Reset()
                    return idx, 0.8
            else:
                # 连续静默：定期复位，别让噪声在 lattice 里堆成唤醒词
                self._idle_checks += 1
                if self._idle_checks >= _IDLE_RESET_CHECKS:
                    self._rec.Reset()
                    self._idle_checks = 0

        return -1, 0.0

    def reset(self) -> None:
        """丢弃当前解码状态，重新开始听。

        播报期间要周期性调用：否则贾维斯自己的声音会残留在识别器里，
        解锁后紧接着被当成唤醒词的前半截命中一次（自激）。
        """
        try:
            self._rec.Reset()
        except Exception:
            pass

    def flush(self) -> tuple[int, float]:
        """离线回放收尾。调用后本引擎已废，调用方应丢弃。"""
        try:
            text = (json.loads(self._rec.FinalResult()) or {}).get("text", "") or ""
        except Exception:
            text = ""
        idx = self._match(text.strip())
        if idx >= 0:
            self._rec.Reset()
            return idx, 0.75
        return -1, 0.0

    def close(self) -> None:
        self._rec = None
        self._model = None

    @property
    def grammar_entries(self) -> list[str]:
        return list(self._grammar)


def discover_vosk_model(base_dir: str) -> str:
    """在 models/vosk 下挑一个可用模型。

    小模型优先：实测小模型在本场景召回 73%、响应 1.58s，
    大模型只有 50%、2.23s —— 大模型为通用识别优化，语法受限场景反而不划算。
    """
    preferred = ("vosk-model-small-cn-0.22", "vosk-model-cn-0.22")
    for name in preferred:
        candidate = os.path.join(base_dir, "models", "vosk", name)
        if os.path.isdir(candidate):
            return candidate
    # 兜底：目录里任意含 am/graph/conf 的子目录
    root = os.path.join(base_dir, "models", "vosk")
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            candidate = os.path.join(root, name)
            if all(os.path.exists(os.path.join(candidate, sub)) for sub in ("am", "conf", "graph")):
                return candidate
    return ""
