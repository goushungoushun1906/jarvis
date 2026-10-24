"""JARVIS 语音唤醒服务 v2 —— 逐帧判定专用关键词，不做"识别完再猜"。

旧实现为什么必须重写
--------------------
旧实现不是"关键词检测"，而是"识别完再猜"：录满 3 秒 → 停 → 送通用语音识别
→ 拿文本 → 正则找「贾维斯」。它有三个没救的结构性问题：

1. 延迟：必须等窗口录满，端到端不会低于 3 秒。
2. 漏检：唤醒词常被切在两个录音窗口的边界，整句丢失。
3. 误报：通用模型把「贾维斯」稳定听成「假为为师」，只能靠音近正则兜，
   排列组合一多就必然又漏又误。

引擎的三次换代（每次都是被真机数据逼的）
----------------------------------------
1. Porcupine：注册要联网验证、AccessKey 拿不到，走不通。且中文必须配
   porcupine_params_zh.pv，配英文的不报错、只是准确率崩掉。
2. sherpa-onnx 3.3M zipformer KWS：免密钥、换词只改一行，本来很理想，
   但在部分笔记本阵列麦上会"自信地判错"—— 真实人声 26 段只命中 2 段（8%），
   而调 boost、调门限、加增益、加滤波全部无效。
3. **Vosk + 受限语法（当前默认）**：完整 LVCSR 声学模型把搜索空间锁死在
   唤醒词上，同样样本召回 73%、误报 0%。详见 app/wakeword_vosk.py。

三个引擎都保留，用 JARVIS_WAKE_ENGINE 切换（vosk / sherpa / porcupine / energy）。

三个反直觉的实现约束（都踩过才知道）
----------------------------------
1. 麦克风不能信系统默认值。Windows 上默认通道经常是一条打得开、读出来却
   全是静音的死通道，必须逐条实测电平再选（见 mic_selector.py）。
2. 打成单文件 exe 后 __file__ 指向解压临时目录的虚拟路径，任何基于包内
   相对位置的推导都会失效，路径必须显式走 _resource_path()。
3. 设备名里的 ® 会让 GBK 日志抛 UnicodeEncodeError 并丢掉**整行**，
   写日志前必须转义（见 _log_safe）。

AccessKey 缺失怎么办
--------------------
**不再降级到能量检测**。旧的那套会把任何说话都当成唤醒，比没有唤醒更糟。
现在会明确报错并告诉用户去哪里填密钥（见 get_info().hint）。
确实需要能量模式时可显式开 JARVIS_WAKE_ALLOW_ENERGY_FALLBACK=1。
"""
from __future__ import annotations

import asyncio
import glob
import io
import json
import logging
import os
import queue
import struct
import sys
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol, Sequence

from . import mic_selector
from . import output_lock
from .wakeword_vosk import (
    VoskWakeEngine,
    build_grammar,
    discover_vosk_model,
)

logger = logging.getLogger("jarvis.wakeword")


class WakeWordStatus(str, Enum):
    IDLE = "idle"            # 未监听
    LISTENING = "listening"  # 正在监听
    SUSPENDED = "suspended"  # 暂挂中（播报/手动录音期间仍在读流，但不判定）
    DETECTED = "detected"    # 刚命中（瞬时状态）
    ERROR = "error"          # 引擎不可用


@dataclass
class DetectionEvent:
    """一次命中的完整记录。

    score 是"推导值"而非引擎置信度：Porcupine 的 process() 只返回命中的
    关键词下标，根本不产出概率（见 _porcupine.py 的 process 文档）。
    这里用双灵敏度一致性 + 电平强度推导，用于诊断与调参。
    """

    keyword: str
    index: int
    score: float
    level: float
    timestamp: float
    engine: str
    sensitivity: float
    capture_id: str | None = None


# ─── 配置 ───────────────────────────────────────────────────────────
#
# 全部字段每次装载时重新读取环境变量。旧实现在模块 import 时读一次，导致
# 用户改完 .env 必须重启后端 —— 现在提供 /reload 热重载。

WAKEWORD_MODEL_DIR = os.path.join("models", "wakeword")
DEFAULT_MODEL_REL = os.path.join(WAKEWORD_MODEL_DIR, "porcupine_params_zh.pv")
KEYWORD_NAMES_REL = os.path.join(WAKEWORD_MODEL_DIR, "keywords.json")

_ENV_ACCESS_KEY = "PORCUPINE_ACCESS_KEY"
_ENV_MODEL = "JARVIS_WAKE_MODEL"
_ENV_KEYWORD_PATHS = "JARVIS_WAKE_KEYWORD_PATHS"
_ENV_SENSITIVITY = "JARVIS_WAKE_SENSITIVITY"
_ENV_DEVICE = "JARVIS_WAKE_DEVICE"
_ENV_COOLDOWN = "JARVIS_WAKE_COOLDOWN"
_ENV_ENERGY_FALLBACK = "JARVIS_WAKE_ALLOW_ENERGY_FALLBACK"
_ENV_DUAL_THRESHOLD = "JARVIS_WAKE_DUAL_THRESHOLD"
_ENV_SAVE_CAPTURES = "JARVIS_WAKE_SAVE_CAPTURES"
_ENV_CAPTURE_DIR = "JARVIS_WAKE_CAPTURE_DIR"
_ENV_ENGINE = "JARVIS_WAKE_ENGINE"

# --- sherpa-onnx（开放词表 KWS，默认引擎）---
#
# 选它作为默认值的原因：不需要账号、AccessKey 或任何训练；换唤醒词只改
# keywords_raw.txt 一行中文（编译成 keywords.txt）。对比之下 Porcupine 虽然
# 精度好，但注册环节要邮箱验证（官方 issue #70 确认验证邮件会被收件方隔离），
# 这条路走不通。
_ENV_SHERPA_MODEL_DIR = "JARVIS_SHERPA_MODEL_DIR"
_ENV_SHERPA_TOKENS = "JARVIS_SHERPA_TOKENS"
_ENV_SHERPA_ENCODER = "JARVIS_SHERPA_ENCODER"
_ENV_SHERPA_DECODER = "JARVIS_SHERPA_DECODER"
_ENV_SHERPA_JOINER = "JARVIS_SHERPA_JOINER"
_ENV_SHERPA_KEYWORDS_FILE = "JARVIS_SHERPA_KEYWORDS_FILE"
_ENV_SHERPA_KEYWORDS_SRC = "JARVIS_SHERPA_KEYWORDS_SRC"
_ENV_SHERPA_THRESHOLD = "JARVIS_SHERPA_THRESHOLD"
_ENV_SHERPA_BOOST = "JARVIS_SHERPA_BOOST"
_ENV_SHERPA_NUM_THREADS = "JARVIS_SHERPA_NUM_THREADS"
_ENV_SHERPA_PROVIDER = "JARVIS_SHERPA_PROVIDER"
_ENV_SHERPA_STRICT_DELTA = "JARVIS_SHERPA_STRICT_DELTA"

# --- Vosk 段 ---
_ENV_VOSK_MODEL = "JARVIS_VOSK_MODEL"
_ENV_VOSK_GRAMMAR = "JARVIS_VOSK_GRAMMAR"

# 默认改用 Vosk：见 VoskWakeEngine 的模块说明。
# 端到端的 3.3M KWS 小模型在部分阵列麦上会"自信地判错"（实测召回 8%），
# 而 Vosk 的 LVCSR + 受限语法在同样样本上是 73% 召回 / 0% 误报。
# 想切回 sherpa 只需设 JARVIS_WAKE_ENGINE=sherpa。
DEFAULT_ENGINE = "vosk"
SHERPA_MODEL_GLOB = "sherpa-onnx-kws-*"

# Porcupine 的灵敏度与 sherpa 的阈值**方向相反**：
# 前者数值越大越容易触发，后者越小越容易触发。这里定义两端的锚点，
# 由 threshold_from_sensitivity 做单调递减映射，让配置与前端始终只面对
# 「灵敏度」一种语义 —— 否则同一套 UI 上会出现两个反向的旋钮，必然搞错。
SHERPA_THRESHOLD_AT_S0 = 0.60   # 灵敏度 0.0 → 最难触发
SHERPA_THRESHOLD_AT_S1 = 0.10   # 灵敏度 1.0 → 最易触发
DEFAULT_SHERPA_SENSITIVITY = 0.70   # → 阈值 0.25，官方建议的起步值
DEFAULT_SHERPA_BOOST = 2.0
DEFAULT_SHERPA_NUM_THREADS = 2
DEFAULT_SHERPA_STRICT_DELTA = 0.10

DEFAULT_SENSITIVITY = 0.6
DEFAULT_COOLDOWN_S = 2.0
RING_SECONDS = 1.5          # 误唤醒留证用：保存命中前这段时间的音频
CAPTURE_KEEP = 50           # 只保留最近这么多条，防止把磁盘吃满
LEVEL_EMIT_INTERVAL_S = 0.1  # 电平事件节流，别用每帧刷前端
# 静音看门狗的观察窗口：这么久一点声音都没有，就认为这条通道是死的
SILENT_WINDOW_S = 8.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _resource_path(relative: str) -> str:
    """定位随包资源。

    打成单文件 exe 后运行时目录与源码目录完全不同：__file__ 指向解压临时
    目录里的虚拟路径。因此这里显式优先用 _MEIPASS，开发态才回退到源码位置。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, relative)
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(server_dir, relative)


def _resolve_vosk_model() -> str:
    """定位 Vosk 模型目录：显式环境变量优先，其次在 models/vosk 下自动挑。"""
    explicit = os.environ.get(_ENV_VOSK_MODEL, "").strip()
    if explicit:
        return _resource_path(explicit)
    return discover_vosk_model(_resource_path(""))


def read_access_key() -> str:
    """读取 AccessKey。

    优先系统凭据管理器（非明文），回退环境变量。与项目里 LLM 密钥的做法
    保持一致：凭据管理器里的值优先，避免用户改了凭据却还在读旧明文。
    """
    try:
        from .secrets import get_secret

        stored = get_secret(_ENV_ACCESS_KEY)
        if stored:
            return stored.strip()
    except Exception:
        # 凭据不可用必须静默降级，不能因为读密钥失败就让整个服务起不来
        pass
    return (os.environ.get(_ENV_ACCESS_KEY, "") or "").strip()


def _default_keyword_paths() -> list[str]:
    """models/wakeword/ 下所有自定义关键词模型。

    没有自训练模型时返回空列表 —— 但**不建议**自动回退到内置英文词：
    中文环境喊英文词只会让用户以为产品坏了。要不要回退由 JARVIS_WAKE_ 显式
    配置决定，错误信息里会说清楚。
    """
    directory = _resource_path(WAKEWORD_MODEL_DIR)
    if not os.path.isdir(directory):
        return []
    return sorted(glob.glob(os.path.join(directory, "*.ppn")))


def _load_keyword_names(paths: list[str]) -> list[str]:
    """关键词的可读名（中文）。

    ppn 文件名必须用 ASCII（中文名在打包路径拼接上有编码坑），所以显示名
    单独放在 keywords.json 里，形如 {"jarvis_zh_windows.ppn": "你好贾维斯"}。
    没有映射文件时退化为去扩展名的文件名。
    """
    mapping: dict[str, str] = {}
    try:
        meta = _resource_path(KEYWORD_NAMES_REL)
        if os.path.isfile(meta):
            with open(meta, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                mapping = {str(k): str(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning("读取关键词名映射失败（不影响唤醒）：%s", e)

    names = []
    for p in paths:
        base = os.path.basename(p)
        names.append(mapping.get(base, os.path.splitext(base)[0]))
    return names


def _parse_sensitivities(count: int) -> list[float]:
    raw = os.environ.get(_ENV_SENSITIVITY, "")
    values: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(min(1.0, max(0.0, float(part))))
        except ValueError:
            continue
    if not values:
        return [DEFAULT_SENSITIVITY] * count
    if len(values) >= count:
        return values[:count]
    # 数量不够就用最后一个值补齐，别因为配少了一个数就整体失效
    return values + [values[-1]] * (count - len(values))


def threshold_from_sensitivity(sensitivity: float) -> float:
    """灵敏度 → sherpa 触发阈值（单调递减）。

    对外只暴露「灵敏度越大越易触发」这一个方向，避免与 Porcupine 的语义
    在 UI 上打架。
    """
    s = min(1.0, max(0.0, float(sensitivity)))
    span = SHERPA_THRESHOLD_AT_S0 - SHERPA_THRESHOLD_AT_S1
    return round(SHERPA_THRESHOLD_AT_S0 - s * span, 4)


def sensitivity_from_threshold(threshold: float) -> float:
    """触发阈值 → 灵敏度，threshold_from_sensitivity 的逆运算。"""
    span = SHERPA_THRESHOLD_AT_S0 - SHERPA_THRESHOLD_AT_S1
    if span <= 0:
        return DEFAULT_SHERPA_SENSITIVITY
    return round(min(1.0, max(0.0, (SHERPA_THRESHOLD_AT_S0 - threshold) / span)), 4)


def _discover_sherpa_model_dir() -> str:
    """models/wakeword/ 下形如 sherpa-onnx-kws-* 的模型目录。

    用 glob 而不是写死目录名，这样升级模型版本不用改代码。
    """
    override = os.environ.get(_ENV_SHERPA_MODEL_DIR, "").strip()
    base = _resource_path(override or WAKEWORD_MODEL_DIR)
    if not os.path.isdir(base):
        return ""
    hits = sorted(
        d
        for d in glob.glob(os.path.join(base, SHERPA_MODEL_GLOB))
        if os.path.isfile(os.path.join(d, "tokens.txt"))
    )
    return hits[-1] if hits else ""


def _load_sherpa_keywords(src: str) -> list[str]:
    """取出关键词的显示名，兼容两种文件格式。

    - 编译产物 keywords.txt：``n ǐ h ǎo ... :2.0 @你好贾维斯``（看 @ 段）
    - 中文源 keywords_raw.txt：一行一个词，没有 @（取整行）

    return 的名字必须与 KeywordSpotter 吐回的 keyword 字符串一致，否则命中了
    也对不上下标 —— 而 @ 段正是 sherpa 回填的那部分。
    """
    names: list[str] = []
    try:
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                display = next((p[1:] for p in parts if p.startswith("@")), "")
                names.append(display or (parts[0] if parts else line))
    except Exception as e:
        logger.warning("读取 sherpa 关键词文件失败：%s", e)
    return names


@dataclass
class WakeConfig:
    """一次生效的唤醒配置快照。"""

    engine: str = DEFAULT_ENGINE      # sherpa | porcupine | energy
    access_key: str = ""
    library_path: str = ""
    model_path: str = ""
    keyword_paths: list[str] = field(default_factory=list)
    keyword_names: list[str] = field(default_factory=list)
    sensitivities: list[float] = field(default_factory=list)
    device_index: int | None = None
    cooldown_s: float = DEFAULT_COOLDOWN_S
    dual_threshold: bool = True
    allow_energy_fallback: bool = False
    save_captures: bool = True
    capture_dir: str = field(default_factory=lambda: os.path.join("logs", "wakeword"))

    # --- sherpa-onnx 段 ---
    sherpa_tokens: str = ""
    sherpa_encoder: str = ""
    sherpa_decoder: str = ""
    sherpa_joiner: str = ""
    sherpa_keywords_file: str = ""     # 编译产物 keywords.txt（拼音 token）
    sherpa_keywords_src: str = ""      # 中文源 keywords_raw.txt（唯一真源）
    sherpa_threshold: float = threshold_from_sensitivity(DEFAULT_SHERPA_SENSITIVITY)
    sherpa_boost: float = DEFAULT_SHERPA_BOOST
    sherpa_num_threads: int = DEFAULT_SHERPA_NUM_THREADS
    sherpa_provider: str = "cpu"
    sherpa_strict_delta: float = DEFAULT_SHERPA_STRICT_DELTA

    # --- Vosk 段 ---
    vosk_model: str = ""
    vosk_grammar_extra: str = ""

    @property
    def has_keywords(self) -> bool:
        if self.engine in ("sherpa", "vosk"):
            return bool(self.keyword_names)
        return bool(self.keyword_paths)

    @property
    def vosk_ready(self) -> bool:
        return bool(self.vosk_model) and os.path.isdir(self.vosk_model)

    @property
    def sherpa_ready(self) -> bool:
        return (
            all(
                os.path.isfile(p)
                for p in (
                    self.sherpa_tokens,
                    self.sherpa_encoder,
                    self.sherpa_decoder,
                    self.sherpa_joiner,
                )
            )
            and bool(self.sherpa_keywords_src)
        )


def _sherpa_file(env_name: str, discovered: str, *patterns: str) -> str:
    """解析 sherpa 模型文件路径：显式环境变量优先，其次在模型目录里按 glob 找。

    用 glob 而不是写死文件名，是因为官方压缩包里的名字带训练配置后缀，例如
    ``encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx`` —— 同一份模型换个
    chunk/left-context 配置后缀就全变了，写死必然在换模型时踩空。候选模式按
    「int8 优先」排序：打包体积和 CPU 推理速度都更划算。
    """
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        return explicit
    if not discovered:
        return ""
    for pattern in patterns:
        for hit in sorted(glob.glob(os.path.join(discovered, pattern))):
            hit = _resource_path(hit)
            if os.path.isfile(hit):
                return hit
    return ""


def _discover_keyword_src(sherpa_dir: str = "") -> str:
    """定位唤醒词真源 keywords_raw.txt。

    优先共享位置 models/wakeword/（与引擎无关），
    回退 sherpa 模型目录里的历史位置——今天文件还放在那里，不能让它失效。
    """
    shared = _resource_path(os.path.join(WAKEWORD_MODEL_DIR, "keywords_raw.txt"))
    if os.path.isfile(shared):
        return shared
    if sherpa_dir:
        for filename in ("keywords_raw.txt", "keywords.txt"):
            candidate = _resource_path(os.path.join(sherpa_dir, filename))
            if os.path.isfile(candidate):
                return candidate
    return ""


def load_wake_config() -> WakeConfig:
    """每次调用都重新读取环境变量 —— 不做任何模块级缓存。"""
    engine = (os.environ.get(_ENV_ENGINE, "").strip().lower() or DEFAULT_ENGINE)

    paths: list[str] = []
    explicit = os.environ.get(_ENV_KEYWORD_PATHS, "")
    if explicit.strip():
        paths = [p.strip() for p in explicit.split(",") if p.strip()]
    else:
        paths = _default_keyword_paths()

    model_path = os.environ.get(_ENV_MODEL, "").strip() or _resource_path(DEFAULT_MODEL_REL)

    # sherpa 的关键词不住在 .ppn 里，而是两个文本文件：
    #   keywords_raw.txt（中文源，给人改） → keywords.txt（拼音 token，给 sherpa 读）
    names = _load_keyword_names(paths)
    sherpa_dir = _discover_sherpa_model_dir()
    sherpa_kw_src = os.environ.get(_ENV_SHERPA_KEYWORDS_SRC, "").strip()
    if engine in ("sherpa", "vosk"):
        # 唤醒词的唯一真源是 keywords_raw.txt，Vosk 与 sherpa 共用同一个文件，
        # 只是各自消费成不同形式（Vosk 要单字语法，sherpa 要拼音 token）。
        # 所以即便引擎是 vosk 也要找到它，否则拿不到唤醒词。
        if not sherpa_kw_src:
            sherpa_kw_src = _discover_keyword_src(sherpa_dir)
        if sherpa_kw_src:
            names = _load_sherpa_keywords(sherpa_kw_src)
            paths = []

    sherpa_kw_file = os.environ.get(_ENV_SHERPA_KEYWORDS_FILE, "").strip()
    if not sherpa_kw_file and sherpa_dir:
        candidate = _resource_path(os.path.join(sherpa_dir, "keywords.txt"))
        if os.path.isfile(candidate):
            sherpa_kw_file = candidate

    # 阈值：显式配置 > 灵敏度推导 > 默认值
    threshold_raw = os.environ.get(_ENV_SHERPA_THRESHOLD, "").strip()
    if threshold_raw:
        try:
            threshold = min(1.0, max(0.0, float(threshold_raw)))
        except ValueError:
            threshold = threshold_from_sensitivity(DEFAULT_SHERPA_SENSITIVITY)
    else:
        # 只在用户**显式**配置灵敏度时才用那个值；否则 sherpa 一律走
        # DEFAULT_SHERPA_SENSITIVITY。否则模型下载前后（关键词列表从空变非空）
        # 默认阈值会悄悄从 0.25 跳到 0.30，调参结论对不上。
        if os.environ.get(_ENV_SENSITIVITY, "").strip():
            sens = _parse_sensitivities(1)[0]
        else:
            sens = DEFAULT_SHERPA_SENSITIVITY
        threshold = threshold_from_sensitivity(sens)

    # sherpa 模式下把灵敏度反推回「与阈值一致」的值再上报，否则诊断面板会显示
    # 一个跟实际阈值对不上的数字（曾经滑杆显示 0.6、真实阈值却是 0.25 对应的
    # 0.7），用户按滑杆调参会得到与预期相反的结果。
    if engine == "sherpa":
        sensitivities = [sensitivity_from_threshold(threshold)] * max(1, len(names))
    else:
        sensitivities = _parse_sensitivities(len(paths or names) or 1)

    return WakeConfig(
        engine=engine,
        access_key=read_access_key(),
        model_path=model_path,
        keyword_paths=paths,
        keyword_names=names,
        sensitivities=sensitivities,
        device_index=_env_int(_ENV_DEVICE, None),
        cooldown_s=_env_float(_ENV_COOLDOWN, DEFAULT_COOLDOWN_S),
        dual_threshold=_env_bool(_ENV_DUAL_THRESHOLD, True),
        allow_energy_fallback=_env_bool(_ENV_ENERGY_FALLBACK, False),
        save_captures=_env_bool(_ENV_SAVE_CAPTURES, True),
        capture_dir=os.environ.get(_ENV_CAPTURE_DIR, "").strip()
        or os.path.join("logs", "wakeword"),
        sherpa_tokens=_sherpa_file(_ENV_SHERPA_TOKENS, sherpa_dir, "tokens.txt"),
        sherpa_encoder=_sherpa_file(
            _ENV_SHERPA_ENCODER,
            sherpa_dir,
            "encoder-epoch-99-avg-1*.int8.onnx",
            "encoder-epoch-99-avg-1*.onnx",
            "encoder-*.int8.onnx",
            "encoder-*.onnx",
        ),
        sherpa_decoder=_sherpa_file(
            _ENV_SHERPA_DECODER,
            sherpa_dir,
            "decoder-epoch-99-avg-1*.int8.onnx",
            "decoder-epoch-99-avg-1*.onnx",
            "decoder-*.int8.onnx",
            "decoder-*.onnx",
        ),
        sherpa_joiner=_sherpa_file(
            _ENV_SHERPA_JOINER,
            sherpa_dir,
            "joiner-epoch-99-avg-1*.int8.onnx",
            "joiner-epoch-99-avg-1*.onnx",
            "joiner-*.int8.onnx",
            "joiner-*.onnx",
        ),
        sherpa_keywords_file=sherpa_kw_file,
        sherpa_keywords_src=sherpa_kw_src,
        sherpa_threshold=threshold,
        sherpa_boost=_env_float(_ENV_SHERPA_BOOST, DEFAULT_SHERPA_BOOST),
        sherpa_num_threads=int(_env_float(_ENV_SHERPA_NUM_THREADS, DEFAULT_SHERPA_NUM_THREADS)),
        sherpa_provider=os.environ.get(_ENV_SHERPA_PROVIDER, "").strip() or "cpu",
        sherpa_strict_delta=_env_float(_ENV_SHERPA_STRICT_DELTA, DEFAULT_SHERPA_STRICT_DELTA),
        vosk_model=_resolve_vosk_model(),
        vosk_grammar_extra=os.environ.get(_ENV_VOSK_GRAMMAR, "").strip(),
    )


# ─── 引擎抽象 ────────────────────────────────────────────────────────
#
# 抽一层是为了将来能换开源引擎（openWakeWord 等）而不动编排层与前端：
# 只要新引擎实现同一个 process/close 契约即可。


def _import_sherpa():
    """导入 sherpa_onnx，并保证 DLL 目录已抢先注册。

    顺序不能反：.pyd 的依赖 DLL 在 LoadLibrary 那一刻就解析完了，
    先 import 再补 add_dll_directory 是无效的。
    """
    from . import sherpa_runtime  # noqa: F401  副作用：注册 DLL 搜索路径

    import sherpa_onnx

    return sherpa_onnx


def _load_token_map(path: str) -> dict[str, int]:
    """解析 tokens.txt 成 {token: id}。

    只能按「最后一段是 id、其余是 token」来切 —— 拼音建模的模型里 token 可能是
    多字符（zh / ch / ǎo），逐字符拆会把它拆碎。
    """
    mapping: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    idx = int(parts[-1])
                except ValueError:
                    continue
                mapping[parts[0]] = idx
    except Exception as e:
        logger.warning("读取 tokens.txt 失败：%s", e)
    return mapping


# 组合声调符 → 声调号（NFD 分解后挂在元音上的那个字符）
_TONE_MARKS = {
    "\u0304": 1,   # ¯ 一声
    "\u0301": 2,   # ´ 二声
    "\u030c": 3,   # ˇ 三声
    "\u0300": 4,   # ` 四声
}


def _strip_tone(token: str) -> tuple[str, int]:
    """带调字符 → (无调骨架, 声调号)。轻声返回 0。"""
    import unicodedata

    tone = 0
    chars: list[str] = []
    for ch in unicodedata.normalize("NFD", token):
        if ch in _TONE_MARKS:
            tone = _TONE_MARKS[ch]
            continue
        chars.append(ch)
    return "".join(chars), tone


def _tone_table(token_map: dict[str, int]) -> dict[tuple[str, int], str]:
    """{（无调韵母, 声调号）: 带调写法}，直接从模型词表反推。

    自己拼声调符号很容易标错位置 —— pypinyin 就会把 wéi 写成 ueí。从词表反推则
    保证产物一定在词表里，拼错的风险被消灭在生成阶段而不是等到识别时才发现。
    """
    table: dict[tuple[str, int], str] = {}
    for token in token_map:
        base, tone = _strip_tone(token)
        if tone:
            table.setdefault((base, tone), token)
    return table


# 声母表。zh/ch/sh 必须排在 z/c/s 前面，否则「shi」会被切成 s + hi
_PINYIN_INITIALS = (
    "zh", "ch", "sh",
    "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "j", "q", "x", "r", "z", "c", "s",
    # y / w 不是真声母，是介音 i / u 的改写（wen = uen、you = iou）。
    # 模型自带样本把「你好问问」写成 ``w èn``，说明它按正字法切，所以这里要认。
    "y", "w",
)


def _split_syllable(syllable: str) -> tuple[str, str]:
    """无声调的完整音节 → (声母, 韵母)，按拼音正字法切。

    刻意用**完整拼写**而不是 pypinyin 的 FINALS：后者会返回音理上的韵母
    （wéi → uei、jūn → ǖn），与模型词表里的书写形式（w + éi、j + ūn）对不上，
    直接照搬会整条词条 OOV。
    """
    syllable = syllable.strip().lower()
    for initial in _PINYIN_INITIALS:
        if syllable.startswith(initial) and len(syllable) > len(initial):
            return initial, syllable[len(initial):]
    return "", syllable


def pinyin_tokens(word: str, token_map: dict[str, int]) -> list[str]:
    """中文词 → 拼音 token 序列，例如「你好」→ ``['n', 'ǐ', 'h', 'ǎo']``。

    依赖 pypinyin，但只在**改过唤醒词之后**第一次启动才被调用；keywords.txt 已经
    是最新的话整条链路不碰拼音库，所以 pypinyin 只进 dev 依赖、不进生产依赖。
    """
    import re

    from pypinyin import Style, pinyin

    table = _tone_table(token_map)
    # NORMAL 给正字法拼写（jun / xue / wei），TONE3 只用来取声调号
    fulls = pinyin(word, style=Style.NORMAL, heteronym=False)
    toned = pinyin(word, style=Style.TONE3, heteronym=False)

    tokens: list[str] = []
    for bare, numbered in zip(fulls, toned):
        syllable = (bare[0] if bare else "").strip()
        if not syllable:
            continue
        match = re.search(r"([1-4])$", (numbered[0] if numbered else "").strip())
        tone = int(match.group(1)) if match else 0

        initial, final = _split_syllable(syllable)
        final_token = table.get((final, tone))
        if not final_token:
            raise ValueError(
                f"音节「{syllable}{tone or ''}」的韵母 {final}({tone}) 不在词表内"
            )
        if initial:
            if initial not in token_map:
                raise ValueError(f"声母「{initial}」不在词表内")
            tokens.append(initial)
        tokens.append(final_token)
    return tokens


def compile_keywords_text(
    words: list[str], token_map: dict[str, int], boost: float
) -> str:
    """中文词列表 → sherpa keywords.txt 的正文。

    每行形如 ``n ǐ h ǎo j iǎ w éi s ī :2.0 @你好贾维斯``，其中 @ 后面的显示名
    就是 KeywordSpotter 命中时回传的字符串。
    """
    lines: list[str] = []
    for word in words:
        tokens = pinyin_tokens(word, token_map)
        # 固定一位小数：官方样本写作 `:2.0`，整数写法虽然也能解析，但保持同构
        lines.append(f"{' '.join(tokens)} :{boost:.1f} @{word}")
    return "\n".join(lines) + "\n"


def _ensure_sherpa_keywords(cfg: WakeConfig) -> str:
    """确保 keywords.txt（喂给 spotter 的产物）与中文源 keywords_raw.txt 同步。

    分工是刻意的：keywords_raw.txt 一行一个中文词，给人改；keywords.txt 一行
    「拼音 token :boost @显示名」，给 sherpa 读。只有源比产物新时才重新编译，
    因此常规启动不需要 pypinyin。

    返回可用路径；编译失败时退回已存在的旧产物（宁可用旧词也不要起不来），
    全都拿不到才返回空串。
    """
    src = cfg.sherpa_keywords_src
    target = cfg.sherpa_keywords_file
    if not target:
        return ""
    if not src or not os.path.isfile(src):
        return target if os.path.isfile(target) else ""

    if os.path.isfile(target):
        try:
            if os.path.getmtime(target) >= os.path.getmtime(src):
                return target
        except OSError:
            pass

    token_map = _load_token_map(cfg.sherpa_tokens)
    if not token_map:
        logger.error("tokens.txt 为空或不可用：%s", cfg.sherpa_tokens)
        return target if os.path.isfile(target) else ""

    try:
        with open(src, "r", encoding="utf-8") as f:
            words = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
        text = compile_keywords_text(words, token_map, cfg.sherpa_boost)
    except ImportError:
        logger.error(
            "keywords_raw.txt 已改动但缺少 pypinyin，无法重新编译；"
            "仍使用现有 keywords.txt。装开发依赖：pip install pypinyin"
        )
        return target if os.path.isfile(target) else ""
    except Exception as e:
        logger.error("编译 sherpa 关键词失败：%s", e)
        return target if os.path.isfile(target) else ""

    for directory in (os.path.dirname(target), cfg.capture_dir or os.path.join("logs", "wakeword")):
        if not directory:
            continue
        try:
            os.makedirs(directory, exist_ok=True)
            out = os.path.join(directory, os.path.basename(target))
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("已根据 %s 重新生成 %s", src, out)
            return out
        except OSError:
            continue
    logger.error("无法写入关键词文件：%s", target)
    return ""


class WakeEngine(Protocol):
    name: str
    sample_rate: int
    frame_length: int

    def process(self, pcm: Sequence[int]) -> tuple[int, float]:
        """返回 (命中下标, 推导分数)。未命中返回 (-1, 0.0)。"""
        ...

    def close(self) -> None: ...


class PorcupineEngine:
    """Porcupine 关键词引擎。

    双实例推导分数：主实例用配置灵敏度决定"是否触发"，严实例（灵敏度更低、
    误报更少）决定"命中强度"。两个实例喂同一帧音频，因此严格一致时说明该
    帧的信号很强。CPU 开销换来可诊断的分数；不需要时可用
    JARVIS_WAKE_DUAL_THRESHOLD=0 关掉。

    注意 Porcupine 的灵敏度语义：**数值越高越容易触发**（漏检少但误报多）。
    所以"严实例"是更低的灵敏度，别搞反。
    """

    def __init__(self, cfg: WakeConfig) -> None:
        import pvporcupine  # 惰性导入：包缺失不应让后端起不来

        self._pv = pvporcupine
        if not cfg.access_key:
            raise RuntimeError("缺少 Picovoice AccessKey")

        common: dict[str, Any] = {
            "access_key": cfg.access_key,
            "keyword_paths": cfg.keyword_paths,
        }
        if cfg.library_path:
            common["library_path"] = cfg.library_path
        if cfg.model_path:
            common["model_path"] = cfg.model_path

        self._base = self._pv.create(sensitivities=cfg.sensitivities, **common)

        self._strict = None
        if cfg.dual_threshold:
            strict_vals = [max(0.0, s - 0.25) for s in cfg.sensitivities]
            try:
                self._strict = self._pv.create(sensitivities=strict_vals, **common)
            except Exception as e:
                # 建第二个实例失败不该让唤醒整体失效，退化成无分数模式
                logger.warning("无法创建严阈值实例，分数将退化为推导值：%s", e)

        self.name = "porcupine"
        self.sample_rate = int(self._base.sample_rate)
        self.frame_length = int(self._base.frame_length)
        self.model_path = cfg.model_path or ""

    def process(self, pcm: Sequence[int]) -> tuple[int, float]:
        idx = self._base.process(pcm)
        if idx < 0:
            return -1, 0.0
        if self._strict is None:
            return idx, 1.0
        strict_idx = self._strict.process(pcm)
        # 严实例也命中 → 信号足够强；只过主实例 →  borderline 命中
        return idx, (1.0 if strict_idx >= 0 else 0.75)

    def close(self) -> None:
        for handle_name in ("_strict", "_base"):
            handle = getattr(self, handle_name, None)
            if handle is None:
                continue
            try:
                handle.delete()
            except Exception:
                pass
            setattr(self, handle_name, None)


class SherpaEngine:
    """sherpa-onnx 开放词表关键词引擎（Zipformer / WenetSpeech 3.3M int8）。

    与 Porcupine 有三处本质差异，决定了这里的写法：

    1. **流式**。不是"喂一帧判一帧"，而是喂进 OnlineStream 后由
       is_ready / decode_stream 驱动解码。内部维护 stream，编排层依旧
       只见 process(pcm)。
    2. **返回关键词字符串而非下标**。KeywordResult 只有 keyword / tokens /
       timestamps 三个字段，**没有任何置信度**，所以要自己把字符串映射回
       下标，名字取 keywords.txt 里 @ 后面的部分。
    3. **命中后必须 reset**。不 reset 的话 get_result() 会一直返回同一个
       关键词，表现为"触发一次之后疯狂自激"。这是 sherpa 最经典的坑，
       因此 reset 写在 process() 内部而不是交给调用方，杜绝遗漏。

    由于拿不到概率，分数沿用 PorcupineEngine 的双实例阶梯规则：两个阈值
    不同的 spotter 喂同一帧，同时命中说明信号确实强。
    """

    def __init__(self, cfg: WakeConfig, spotter_factory: Callable | None = None) -> None:
        sherpa = _import_sherpa()

        self._sherpa = sherpa
        self._min_threshold = min(1.0, max(0.0, cfg.sherpa_threshold))
        self._index = {name: i for i, name in enumerate(cfg.keyword_names)}

        if spotter_factory is None:
            self._keywords_file = _ensure_sherpa_keywords(cfg)
            spotter_factory = self._default_spotter_factory(cfg)
        else:
            # 测试注入路径：不碰磁盘与 onnx
            self._keywords_file = cfg.sherpa_keywords_file

        self._spotter = spotter_factory(self._min_threshold)
        self._stream = self._spotter.create_stream()

        self._strict = None
        self._strict_stream = None
        if cfg.dual_threshold:
            strict_threshold = min(0.95, self._min_threshold + cfg.sherpa_strict_delta)
            if strict_threshold > self._min_threshold:
                try:
                    self._strict = spotter_factory(strict_threshold)
                    self._strict_stream = self._strict.create_stream()
                except Exception as e:
                    # 建第二个实例失败不该让唤醒整体失效，退化成单实例
                    logger.warning("无法创建严阈值 spotter，分数将退化为统一值：%s", e)

        self.name = "sherpa"
        self.sample_rate = 16000
        # 对齐 Porcupine / EnergyVad，使 _capture_loop 的分帧与环形缓冲计算无需改动
        self.frame_length = 512
        self.model_path = cfg.sherpa_keywords_src or ""

    def _default_spotter_factory(self, cfg: WakeConfig) -> Callable:
        cfg_keywords = self._keywords_file

        def make(threshold: float):
            return self._sherpa.KeywordSpotter(
                tokens=cfg.sherpa_tokens,
                encoder=cfg.sherpa_encoder,
                decoder=cfg.sherpa_decoder,
                joiner=cfg.sherpa_joiner,
                keywords_file=cfg_keywords,
                keywords_score=cfg.sherpa_boost,
                keywords_threshold=threshold,
                num_threads=cfg.sherpa_num_threads,
                provider=cfg.sherpa_provider,
            )

        return make

    @staticmethod
    def _to_float32(pcm: Sequence[int]):
        """int16 → 归一化 float32。

        accept_waveform 收 int16 不会报错，但特征会全部溢出、导致永远检测
        不到 —— 这是个静默失败，务必转换。
        """
        import numpy as np

        return np.asarray(pcm, dtype=np.float32) / 32768.0

    def process(self, pcm: Sequence[int]) -> tuple[int, float]:
        samples = self._to_float32(pcm)

        self._stream.accept_waveform(self.sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
        keyword = (self._spotter.get_result(self._stream) or "").strip()

        strict_keyword = ""
        if self._strict_stream is not None:
            self._strict_stream.accept_waveform(self.sample_rate, samples)
            while self._strict.is_ready(self._strict_stream):
                self._strict.decode_stream(self._strict_stream)
            strict_keyword = (self._strict.get_result(self._strict_stream) or "").strip()

        if not keyword and not strict_keyword:
            return -1, 0.0

        index = self._index.get(keyword or strict_keyword, 0)
        score = 1.0 if (keyword and strict_keyword) else 0.75

        # 必须清理，否则下一次循环还会拿到同一个结果。
        # API 名是 reset_stream 而不是 reset —— 后者在 KeywordSpotter 上不存在，
        # 写错要到真机命中那一刻才炸，而命中路径恰恰是单测替身最容易漏掉的。
        self._spotter.reset_stream(self._stream)
        if self._strict_stream is not None and strict_keyword:
            self._strict.reset_stream(self._strict_stream)
        return index, score

    def flush(self) -> tuple[int, float]:
        """离线回放收尾：补尾部静音把缓冲区解完。

        短音频里唤醒词紧贴文件末尾时，不补这段会导致最后一段特征永远不被
        解码而漏检。调用后本 stream 已废，调用方应丢弃本引擎实例。
        """
        import numpy as np

        tail = np.zeros(int(0.66 * self.sample_rate), dtype=np.float32)
        self._stream.accept_waveform(self.sample_rate, tail)
        self._stream.input_finished()
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
        keyword = (self._spotter.get_result(self._stream) or "").strip()
        if not keyword:
            return -1, 0.0
        self._spotter.reset_stream(self._stream)
        return self._index.get(keyword, 0), 0.75

    def close(self) -> None:
        for attr in ("_strict", "_spotter"):
            setattr(self, attr, None)
        self._stream = None
        self._strict_stream = None


class EnergyVadEngine:
    """能量兜底引擎。默认关闭 —— 它只是"有人说话"，不是"叫贾维斯"。

    保留它是为了麦克风彻底不可用时还能演示链路，但绝不自动降级：
    任何说话都触发的唤醒，比没有唤醒更让人恼火。
    """

    name = "energy_vad"
    sample_rate = 16000
    frame_length = 512

    def __init__(self, cfg: WakeConfig) -> None:
        self._threshold = 1500
        self._speech_frames = 0
        self._silence_frames = 0

    def process(self, pcm: Sequence[int]) -> tuple[int, float]:
        import array

        samples = array.array("h", pcm)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0.0
        if rms > self._threshold:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            if self._silence_frames > 30 and self._speech_frames > 10:
                self._speech_frames = 0
                self._silence_frames = 0
                return 0, 0.3
        return -1, 0.0

    def close(self) -> None:
        pass


def _rms_of_frame(raw: bytes) -> float:
    """把一帧 16bit 单声道采样换算成 0..1 的电平值。"""
    if not raw:
        return 0.0
    try:
        samples = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
    except struct.error:
        return 0.0
    if not samples:
        return 0.0
    energy = sum(s * s for s in samples) / len(samples)
    return min(1.0, (energy**0.5) / 32768.0)


# ─── 麦克风枚举 ──────────────────────────────────────────────────────


def _is_likely_builtin(name: str) -> bool:
    """猜测是不是笔记本内置麦克风。只用于界面上的排序与提示。"""
    lowered = name.lower()
    markers = (
        "内置", "internal", "realtek", "conexant",
        "麦克风阵列", "microphone array",
    )
    return any(m in name or m in lowered for m in markers)


def list_input_devices() -> list[dict[str, Any]]:
    """列出可用输入设备。实现挪到了 mic_selector，这里保留同名入口给路由与测试。"""
    return mic_selector.list_input_devices()


def _log_safe(text: str) -> str:
    """日志用的安全文本：换掉窄编码（GBK）写不进去的字符。

    中文 Windows 的控制台/重定向文件默认是 GBK，而麦克风设备名里带 ®
    （「英特尔® 智音」）。logging 遇到写不进去的字符不会只丢那个字——
    它会抛 UnicodeEncodeError，**整条日志行**都写不出来，控制台上只留一句
    "--- Logging error ---"。日志里少一个 ® 无所谓，丢掉整行（尤其是刚打印
    的设备选择结果）会让排查变瞎。
    """
    if not text:
        return text
    return text.replace("\xae", "(R)").replace("\u2122", "(TM)")


# ─── 服务编排 ────────────────────────────────────────────────────────


def _vosk_grammar_preview(cfg: "WakeConfig") -> list[str]:
    """预览 Vosk 将要使用的语法条目（最多 8 条）。

    暴露出来是因为中文语法必须以**单字序列**书写，写错不会报错、
    只会静默失效（0 命中）。让用户能在诊断面板上直接看到实际条目，
    一眼判断是不是语法没生成对。
    """
    if not cfg.keyword_names:
        return []
    try:
        return build_grammar(cfg.keyword_names)[:8]
    except Exception:
        return []


class WakeWordService:
    """采集线程 + 事件扇出的编排层，本身不做任何音频判定。"""

    ERROR_NO_ENGINE = "未安装 pvporcupine，无法启用关键词唤醒"
    ERROR_NO_SHERPA_MODEL = (
        "缺少 sherpa-onnx 唤醒模型。请执行 "
        "python scripts/dev/download_sherpa_kws.py 下载，或手动放置到 "
        "server/models/wakeword/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/"
    )
    ERROR_NO_KEY = (
        "未配置 Picovoice AccessKey。请在 .env 中设置 PORCUPINE_ACCESS_KEY，"
        "或在设置页把密钥存进系统凭据管理器"
    )
    ERROR_NO_MODEL = (
        "缺少中文唤醒模型。请把 porcupine_params_zh.pv 与自训练的 "
        "jarvis_zh_windows.ppn 放到 server/models/wakeword/ 下"
    )
    ERROR_NO_KEYWORDS = (
        "没有配置唤醒词。请在 server/models/wakeword/ 下的 keywords_raw.txt "
        "里写一行中文唤醒词（例如「你好贾维斯」）"
    )
    ERROR_NO_PORCUPINE_KEYWORDS = (
        "未找到任何关键词模型（.ppn）。请先在 Picovoice Console 训练"
        "「你好贾维斯」并放到 server/models/wakeword/ 下"
    )
    ERROR_NO_VOSK_MODEL = (
        "缺少 Vosk 中文模型。请到 https://alphacephei.com/vosk/models 下载 "
        "vosk-model-small-cn-0.22 并解压到 server/models/vosk/ 下，"
        "或设置 JARVIS_VOSK_MODEL 指向模型目录"
    )

    def __init__(
        self,
        config_loader: Callable[[], WakeConfig] = load_wake_config,
        engine_factory: Callable[[WakeConfig], WakeEngine] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._engine_factory = engine_factory

        self._status = WakeWordStatus.IDLE
        self._error: str | None = None
        self._hint: str | None = None

        self._cfg: WakeConfig = config_loader()
        self._engine: WakeEngine | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._suspend_event = threading.Event()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()

        # 诊断用统计
        self._detections_total = 0
        self._false_alarms_marked = 0
        self._peak_confidence = 0.0
        self._peak_level = 0.0
        self._recent_peaks: deque[dict[str, float]] = deque(maxlen=500)
        self._last_detection_ts = 0.0
        self._captures: list[dict[str, Any]] = []

        # 麦克风自检结果。`device` 只说明"选中了谁"，这三个说明"它到底有没
        # 有声音" —— 打得开却全是静音的通道在 Windows 上太常见了，不把电平
        # 暴露出来，用户只能看到"一切正常但喊不醒"。
        self._mic_name: str | None = None
        self._mic_index: int | None = None
        self._mic_rms: float = 0.0
        self._mic_silent: bool = False
        # 启动中标记。采集线程是**异步**把状态置成 listening 的，而前端的两个
        # 订阅循环会在极短时间内先后各调一次 /start；只靠 status 判断会同时
        # 起两个引擎、开两条麦克风流（日志里出现过两行 engine=sherpa），
        # 在 Windows 上两条流抢同一个阵列麦会直接把进程拖死。
        self._starting = False

    # ── 对外状态 ──────────────────────────────────────────────────

    @property
    def status(self) -> WakeWordStatus:
        return self._status

    @property
    def error(self) -> str | None:
        return self._error

    def _set_status(self, status: WakeWordStatus, reason: str | None = None) -> None:
        self._status = status
        self._emit(
            "status",
            {
                "status": status.value,
                "engine": self._engine.name if self._engine else None,
                "reason": reason,
                "error": self._error,
            },
        )

    # ── 生命周期 ──────────────────────────────────────────────────

    async def start(
        self,
        device_index: int | None = None,
        sensitivities: list[float] | None = None,
    ) -> dict[str, Any]:
        if self._status in (WakeWordStatus.LISTENING, WakeWordStatus.SUSPENDED):
            if self._thread and self._thread.is_alive():
                return dict(self.get_info())
        # 上一个 start 还没把线程拉起来：别再起第二个，直接返回当前状态
        if self._starting:
            return dict(self.get_info())

        self._cfg = self._config_loader()
        if device_index is not None:
            self._cfg.device_index = device_index
        if sensitivities:
            self._cfg.sensitivities = [
                min(1.0, max(0.0, float(s))) for s in sensitivities
            ]

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # 只有 Porcupine 才需要 AccessKey。sherpa 是本地开放词表模型，
        # 不校验密钥 —— 少了这个分支，默认引擎会永远停在 ERROR 态。
        if self._cfg.engine == "porcupine" and not self._cfg.access_key:
            self._fail(self.ERROR_NO_KEY, "请填写 AccessKey 后调用 /api/wakeword/reload")
            return dict(self.get_info())

        self._starting = True
        try:
            self._engine = self._create_engine(self._cfg)
        except Exception as e:
            self._starting = False
            self._fail(str(e), self._error_hint(e))
            return dict(self.get_info())

        self._error = None
        self._hint = None
        self._mic_silent = False
        self._mic_index = None
        self._mic_name = None
        self._stop_event.clear()
        self._suspend_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="jarvis-wakeword"
        )
        self._thread.start()
        # 状态不在此时置位：由采集线程流真正打开后置位，避免接口返回假可用
        return dict(self.get_info())

    async def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        self._close_engine()
        self._status = WakeWordStatus.IDLE
        self._emit("status", {"status": self._status.value})
        return dict(self.get_info())

    async def reload(self) -> dict[str, Any]:
        """热重读配置（含 AccessKey），无需重启后端。

        旧实现把 AccessKey 在模块 import 时读一次，用户在界面上填完还得重启
        整个应用才能生效 —— 这是那条一次求值最重要的实际代价。
        """
        was_running = self._status in (
            WakeWordStatus.LISTENING,
            WakeWordStatus.SUSPENDED,
        )
        await self.stop()
        result = await self.start()
        self._emit(
            "config",
            {
                "keywords": self._cfg.keyword_names,
                "keyword_paths": self._cfg.keyword_paths,
                "sensitivities": self._cfg.sensitivities,
                "device": self._cfg.device_index,
                "engine": self._engine.name if self._engine else None,
                "was_running": was_running,
            },
        )
        return result

    async def suspend(self, reason: str = "external") -> dict[str, Any]:
        """暂停判定但**不关闭音频流**。

        关流再开在 Windows 上会撞设备独占，而且要几百毫秒才能重新稳定。
        保持读流、只跳过判定，播报一结束就能立刻恢复。
        """
        self._suspend_event.set()
        if self._status == WakeWordStatus.LISTENING:
            self._set_status(WakeWordStatus.SUSPENDED, reason)
        return dict(self.get_info())

    async def resume(self) -> dict[str, Any]:
        self._suspend_event.clear()
        if self._status == WakeWordStatus.SUSPENDED and self._thread and self._thread.is_alive():
            self._set_status(WakeWordStatus.LISTENING)
        return dict(self.get_info())

    async def set_sensitivities(self, values: list[float]) -> dict[str, Any]:
        """运行时改灵敏度，不中断采集。

        Porcupine 不支持运行中改灵敏度，所以要重建引擎实例 —— 但采集线程
        与音频流保持不动，用户感受是连续的。
        """
        if not values:
            raise ValueError("灵敏度不能为空")
        cleaned = [min(1.0, max(0.0, float(v))) for v in values]
        self._cfg.sensitivities = cleaned
        if self._engine is None:
            return dict(self.get_info())

        # Porcupine 实例持有原生句柄，换实例前必须显式关闭，否则每次调一次
        # 灵敏度就泄漏一份。反过来，新实例建失败时要保住旧的继续可用 ——
        # 仅仅因为滑杆拖到一个非法值就把唤醒搞挂，代价太大。
        previous = self._engine
        try:
            self._engine = self._create_engine(self._cfg)
        except Exception as e:
            self._engine = previous
            self._fail(str(e), self._error_hint(e))
            return dict(self.get_info())
        try:
            previous.close()
        except Exception:
            logger.debug("关闭旧唤醒实例失败，忽略", exc_info=True)
        self._emit(
            "config",
            {
                "keywords": self._cfg.keyword_names,
                "sensitivities": cleaned,
                "engine": self._engine.name,
            },
        )
        return dict(self.get_info())

    # ── 订阅 ──────────────────────────────────────────────────────

    def subscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs.add(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(queue)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """线程安全地推送事件到事件循环。"""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event_type, payload)
        except RuntimeError:
            pass  # 事件循环已关闭，丢弃即可

    def _fanout(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subs)
        for queue in targets:
            try:
                if queue.full():
                    # 慢订阅者丢最旧事件，绝不阻塞音频采集线程
                    queue.get_nowait()
                queue.put_nowait({"type": event_type, "payload": payload})
            except Exception:
                continue

    # ── 引擎构建 ──────────────────────────────────────────────────

    def _create_engine(self, cfg: WakeConfig) -> WakeEngine:
        if self._engine_factory is not None:
            return self._engine_factory(cfg)

        name = (cfg.engine or DEFAULT_ENGINE).strip().lower()

        if name in ("energy", "energy_vad"):
            if not cfg.allow_energy_fallback:
                raise RuntimeError(
                    "能量兜底引擎默认关闭（它只能判断「有人在说话」，"
                    "会把任何语音都当成唤醒）。确实需要请设置 "
                    "JARVIS_WAKE_ALLOW_ENERGY_FALLBACK=1"
                )
            return EnergyVadEngine(cfg)

        if name == "vosk":
            if not cfg.vosk_ready:
                raise RuntimeError(self.ERROR_NO_VOSK_MODEL)
            if not cfg.keyword_names:
                raise RuntimeError(self.ERROR_NO_KEYWORDS)
            return VoskWakeEngine(cfg)

        if name == "sherpa":
            if not cfg.sherpa_ready:
                raise RuntimeError(self.ERROR_NO_SHERPA_MODEL)
            return SherpaEngine(cfg)

        if name == "porcupine":
            return self._create_porcupine_engine(cfg)

        raise RuntimeError(
            f"未知的唤醒引擎：{name}（可选 vosk / sherpa / porcupine / energy）"
        )

    def _create_porcupine_engine(self, cfg: WakeConfig) -> WakeEngine:
        try:
            import pvporcupine  # noqa: F401
        except ImportError as e:
            if cfg.allow_energy_fallback:
                logger.warning("%s，降级到能量检测", self.ERROR_NO_ENGINE)
                return EnergyVadEngine(cfg)
            raise RuntimeError(self.ERROR_NO_ENGINE) from e

        if not cfg.keyword_paths:
            raise RuntimeError(self.ERROR_NO_PORCUPINE_KEYWORDS)
        if cfg.model_path and not os.path.isfile(cfg.model_path):
            raise RuntimeError(self.ERROR_NO_MODEL)

        # 库路径显式给出：打包后 pvporcupine 自己的路径推导会失效
        cfg.library_path = cfg.library_path or self._resolve_pv_library()
        return PorcupineEngine(cfg)

    def _resolve_pv_library(self) -> str:
        """定位 libpv_porcupine.dll，定位不到返回空串交给 Porcupine 自己处理。"""
        try:
            import pvporcupine

            pkg_dir = os.path.dirname(pvporcupine.__file__)
            candidate = os.path.join(
                pkg_dir, "lib", "windows", "amd64", "libpv_porcupine.dll"
            )
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            pass
        return ""

    def _close_engine(self) -> None:
        engine = self._engine
        self._engine = None
        if engine is None:
            return
        try:
            engine.close()
        except Exception:
            pass

    def _fail(self, message: str, hint: str | None = None) -> None:
        logger.error("唤醒引擎不可用：%s", message)
        self._error = message
        self._hint = hint
        self._status = WakeWordStatus.ERROR
        self._emit(
            "error",
            {"error": message, "hint": hint, "fatal": False},
        )

    def _error_hint(self, exc: Exception) -> str | None:
        name = exc.__class__.__name__
        if "ActivationLimit" in name or "Throttled" in name:
            return "Picovoice 免费额度已用尽，请到 Picovoice Console 查看"
        if "Activation" in name:
            return "AccessKey 无效或被拒绝，请到 Picovoice Console 确认"
        return None

    # ── 采集线程 ──────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        """采集线程入口。

        包一层 try/finally 是为了**一定**把 ``_starting`` 复位。这个标记挡着
        第二个 start，一旦卡在 True 上，唤醒就再也起不来了 —— 属于"一次异常
        永久失效"的坑，必须兜住。
        """
        try:
            self._capture_loop_inner()
        finally:
            self._starting = False

    def _capture_loop_inner(self) -> None:
        engine = self._engine
        if engine is None:
            self._fail(self.ERROR_NO_ENGINE)
            return

        try:
            import pyaudio as pa
        except ImportError as e:
            self._fail("未安装 pyaudio，无法采集麦克风", str(e))
            return

        # 「打得开但全是静音」是最难自查的一类故障：日志一切正常、状态是
        # listening、电平事件也在推，可用户怎么喊都不唤醒。所以监听期间持续
        # 盯住电平，确认是死通道就换下一条再试，别让用户对着静音喊半天。
        max_attempts = 3
        for attempt in range(max_attempts):
            if self._stop_event.is_set():
                break
            try:
                stream, py_audio, device, frames = self._open_stream(pa, engine)
            except Exception as e:
                self._fail(f"打开麦克风失败：{e}", "请检查麦克风是否被其它程序独占")
                return

            try:
                verdict = self._run_capture(stream, engine, device, frames)
            finally:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                try:
                    py_audio.terminate()
                except Exception:
                    pass

            if verdict != "silent":
                break
            if self._cfg.device_index is not None:
                break  # 用户显式指定过设备，不擅自替他换
            mic_selector.mark_dead(device)
            if attempt + 1 < max_attempts:
                logger.warning("麦克风通道 %s 持续无声音，换下一条重试", device)

        if self._mic_silent and not self._stop_event.is_set():
            self._fail(
                "麦克风没有收到任何声音",
                "请检查 Windows 设置 → 隐私和安全性 → 麦克风："
                "「允许桌面应用访问麦克风」需打开，且输入音量不能为 0",
            )
        if self._status != WakeWordStatus.IDLE:
            self._status = WakeWordStatus.IDLE
        logger.info("唤醒监听已停止")

    def _run_capture(
        self,
        stream,
        engine: WakeEngine,
        device: int | None,
        frames: "queue.Queue[bytes]",
    ) -> str:
        """跑一条通道的采集循环。

        返回 "stop"（正常退出）、"silent"（确认是死通道，请换一条）、
        "error"（读流出错，不要再试）。

        音频来自回调线程写入的队列，而不是主循环里的 stream.read()。
        理由见 _open_stream 的注释：阻塞 read 在这台阵列麦上会拿回削顶信号。
        """
        self._set_status(WakeWordStatus.LISTENING)
        logger.info(
            "唤醒监听已启动：engine=%s rate=%d frame=%d device=%s keywords=%s sensitivity=%s",
            engine.name,
            engine.sample_rate,
            engine.frame_length,
            device,
            self._cfg.keyword_names,
            self._cfg.sensitivities,
        )

        formatter = struct.Struct(f"<{engine.frame_length}h")
        ring_size = max(1, int(RING_SECONDS * engine.sample_rate / engine.frame_length))
        ring: deque[bytes] = deque(maxlen=ring_size)
        last_level_emit = 0.0
        window_start = time.time()
        window_peak = 0.0

        stream.start_stream()

        try:
            while not self._stop_event.is_set():
                # 引擎可能被 set_sensitivities 换掉，每帧取最新引用（赋值是原子的）
                engine = self._engine or engine

                # 带超时是为了退出时不被 get() 永久堵住：
                # stop_event 置位后最多再等这么久就能干净地退出线程
                try:
                    raw = frames.get(timeout=0.25)
                except queue.Empty:
                    continue

                if len(raw) < engine.frame_length * 2:
                    continue

                ring.append(raw)
                now = time.time()

                level = _rms_of_frame(raw)
                if level > window_peak:
                    window_peak = level
                if now - last_level_emit >= LEVEL_EMIT_INTERVAL_S:
                    last_level_emit = now
                    self._peak_level = max(self._peak_level * 0.9, level)
                    self._emit("level", {"rms": round(level, 4), "ts": now})

                # 静音看门狗：整整一个窗口连环境底噪都没有 → 这条通道是死的
                if now - window_start >= SILENT_WINDOW_S:
                    if window_peak < mic_selector.SILENT_RMS:
                        self._mic_silent = True
                        logger.warning(
                            "麦克风 %.0f 秒内完全没有声音（峰值 %.5f），判定为无效通道",
                            SILENT_WINDOW_S,
                            window_peak,
                        )
                        return "silent"
                    self._mic_silent = False
                    window_start = now
                    window_peak = 0.0

                if self._suspend_event.is_set():
                    continue

                # 播报期间把耳朵闭上。笔记本麦离扬声器才十几厘米，自己的
                # 声音会被稳定听成唤醒词 → 打断自己 → 重播 → 再听，死循环。
                # 光靠前端 suspend 不够：浏览器那边合成有 1~2 秒空档
                # （字节到了才置 isPlaying），那几秒足够把播报声灌进来。
                if output_lock.is_output_active():
                    ring.clear()
                    resetter = getattr(engine, "reset", None)
                    if callable(resetter):
                        try:
                            resetter()
                        except Exception:
                            pass
                    continue

                try:
                    index, score = engine.process(formatter.unpack(raw[: engine.frame_length * 2]))
                except Exception as e:
                    logger.warning("唤醒引擎处理异常：%s", e)
                    continue

                if index >= 0:
                    self._on_detection(index, score, level, list(ring))
        except Exception as e:
            logger.error("唤醒采集异常：%s", e)
            return "error"
        return "stop"

    def _open_stream(self, pa, engine: WakeEngine):
        """打开采集流。

        **必须用回调模式，不能用阻塞 read()**。本机实测（英特尔智音阵列麦，
        device 5，同一时刻同一台机器）：

            阻塞 read(512)   → rms 0.03987  peak **1.0000**  DC +0.00109
            阻塞 read(1024)  → rms 0.14162  peak **1.0000**  DC +0.02390
            回调 512 帧/次   → rms 0.00540  peak 0.0460     DC -0.00026
            回调 1024 帧/次  → rms 0.00378  peak 0.0244     DC -0.00007

        阻塞读拿回来的信号恒为满刻度削顶，还带直流偏置。原因是驱动缓冲一旦
        溢出，PortAudio 默认会把样本**钳位**到 ±32767 而不是丢弃。
        而削顶会给信号注入大量谐波 + 直流，等于把唤醒模型的输入直接污染掉
        —— 这正是「频谱中频被掏空」的成因之一，也是"日志全绿却怎么喊都不醒"
        的物理根源。回调模式由 PortAudio 自己的线程按驱动节奏取数，不会溢出，
        拿到的才是干净信号。
        """
        py_audio = pa.PyAudio()
        frames: "queue.Queue[bytes]" = queue.Queue(maxsize=128)

        def _callback(in_data, frame_count, time_info, status):
            # 回调里只入队，绝不做任何音频处理：卡住回调线程就等于丢数据
            try:
                frames.put_nowait(in_data)
            except queue.Full:
                pass  # 处理跟不上时宁可丢帧，也不要让回调阻塞
            return (None, pa.paContinue)

        kwargs: dict[str, Any] = {
            "format": pa.paInt16,
            "channels": 1,
            "rate": engine.sample_rate,
            "input": True,
            "frames_per_buffer": engine.frame_length,
            "stream_callback": _callback,
        }
        device = self._resolve_device()
        if device is not None:
            kwargs["input_device_index"] = device
        try:
            stream = py_audio.open(**kwargs)
        except Exception:
            # 指定了设备却打不开（换了接口/被拔掉）时回退默认设备，
            # 否则用户会卡在"选了一次就再也起不来"
            kwargs.pop("input_device_index", None)
            device = None
            self._mic_name = None
            self._mic_index = None
            stream = py_audio.open(**kwargs)
        return stream, py_audio, device, frames

    def _resolve_device(self) -> int | None:
        """决定用哪条输入通道。

        用户显式指定过就尊重他；否则**不信任系统默认**——Windows 上默认设备
        经常是一条打得开、读出来却全是静音的通道（详见 mic_selector 的说明）。
        这里逐条探测实际电平后再选。
        """
        if self._cfg.device_index is not None:
            return self._cfg.device_index

        pick = mic_selector.pick_input_device()
        index = pick.get("index")
        self._mic_index = index
        self._mic_name = pick.get("name")
        self._mic_rms = float(pick.get("rms") or 0.0)
        self._mic_silent = bool(pick.get("silent"))
        if index is None:
            logger.warning(
                "没有任何麦克风通道能收到声音（系统可能禁止了麦克风访问），唤醒将无输入"
            )
            return None
        logger.info(
            "麦克风自动选择：device=%s name=%s rms=%.5f %s",
            index,
            _log_safe(self._mic_name or ""),
            self._mic_rms,
            "（电平偏低，请确认系统未静音）" if self._mic_silent else "",
        )
        return index

    def _on_detection(
        self,
        index: int,
        score: float,
        level: float,
        ring: list[bytes],
    ) -> None:
        now = time.time()
        if now - self._last_detection_ts < self._cfg.cooldown_s:
            return
        self._last_detection_ts = now

        names = self._cfg.keyword_names
        keyword = names[index] if 0 <= index < len(names) else str(index)
        sensitivity = (
            self._cfg.sensitivities[index]
            if 0 <= index < len(self._cfg.sensitivities)
            else DEFAULT_SENSITIVITY
        )

        capture_id = None
        if self._cfg.save_captures:
            capture_id = self._save_capture(ring, keyword, score)

        self._detections_total += 1
        self._peak_confidence = max(score, self._peak_confidence * 0.9)
        self._recent_peaks.append({"ts": now, "score": score, "level": level})

        logger.info(
            "唤醒命中：keyword=%s score=%.2f level=%.2f capture=%s",
            keyword,
            score,
            level,
            capture_id,
        )
        self._emit(
            "detection",
            {
                "keyword": keyword,
                "index": index,
                "score": round(score, 3),
                "level": round(level, 3),
                "sensitivity": sensitivity,
                "engine": self._engine.name if self._engine else None,
                "ts": now,
                "capture_id": capture_id,
                "cooldown_s": self._cfg.cooldown_s,
            },
        )
        self._set_status(WakeWordStatus.LISTENING)

    # ── 误唤醒留证 ────────────────────────────────────────────────

    def _capture_dir(self) -> str:
        return _resource_path(self._cfg.capture_dir)

    def _save_capture(self, ring: list[bytes], keyword: str, score: float) -> str | None:
        """把命中前 1.5 秒写成音频文件，用于回放确认真的是不是误报。

        为什么必须存：误唤醒这事儿没法从日志里推理。用户说"它自己叫了"，
        没有录音就永远只能是猜测。
        """
        try:
            directory = self._capture_dir()
            os.makedirs(directory, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            capture_id = f"{stamp}-{keyword}-{int(score * 100)}"
            path = os.path.join(directory, f"{capture_id}.wav")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"".join(ring))
            self._captures.insert(
                0,
                {
                    "id": capture_id,
                    "path": path,
                    "ts": time.time(),
                    "keyword": keyword,
                    "score": score,
                    "label": None,
                },
            )
            self._captures = self._captures[:CAPTURE_KEEP]
            return capture_id
        except Exception as e:
            logger.warning("保存唤醒留证失败：%s", e)
            return None

    def get_captures(self) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in c.items() if k != "path"} for c in self._captures
        ]

    def get_capture_path(self, capture_id: str) -> str | None:
        for item in self._captures:
            if item["id"] == capture_id:
                return item["path"]
        candidate = os.path.join(self._capture_dir(), f"{capture_id}.wav")
        return candidate if os.path.isfile(candidate) else None

    def mark_capture(self, capture_id: str, label: str) -> bool:
        if label not in {"false_alarm", "true_wake"}:
            return False
        for item in self._captures:
            if item["id"] == capture_id:
                item["label"] = label
                if label == "false_alarm":
                    self._false_alarms_marked += 1
                return True
        return False

    # ── 离线回放测试 ──────────────────────────────────────────────

    def _detect_wav(self, wav_bytes: bytes) -> dict[str, Any]:
        """离线跑一段音频，不占用麦克风。

        这个能力是**打包验证**的关键：验证脚本无法在开发机上对着 exe 喊话，
        只能上传一段音频断言能被识别到。

        这里刻意**新建一次性引擎**而不复用 self._engine：
        sherpa 是有内部状态的流式解码器，把一段测试音频灌进正在监听的
        stream，等于往实时上下文里插了一段话，会污染后续判定、甚至直接
        造成一次虚假唤醒。顺带也修好了「未 /start 时 /test 必然报错」的老问题。
        """
        engine = self._create_engine(self._cfg)
        try:
            return self._detect_wav_with(engine, wav_bytes)
        finally:
            try:
                engine.close()
            except Exception:
                pass

    def _detect_wav_with(self, engine: WakeEngine, wav_bytes: bytes) -> dict[str, Any]:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ValueError("只支持 16bit 单声道 wav")
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if rate != engine.sample_rate:
            raise ValueError(
                f"采样率需为 {engine.sample_rate}Hz，实际 {rate}Hz"
            )

        formatter = struct.Struct(f"<{engine.frame_length}h")
        step = engine.frame_length * 2
        best_score = 0.0
        for offset in range(0, len(frames) - step + 1, step):
            try:
                index, score = engine.process(formatter.unpack(frames[offset : offset + step]))
            except Exception:
                continue
            if index >= 0:
                names = self._cfg.keyword_names
                keyword = names[index] if 0 <= index < len(names) else str(index)
                return {
                    "hit": True,
                    "keyword": keyword,
                    "index": index,
                    "score": round(score, 3),
                    "offset_ms": round(offset / 2 / rate * 1000, 1),
                }
            best_score = max(best_score, score)

        # 流式引擎缓冲区里还压着最后一小段特征，不 flush 会漏掉
        # 「唤醒词紧贴文件末尾」这类短音频
        hook = getattr(engine, "flush", None)
        if callable(hook):
            try:
                index, score = hook()
            except Exception:
                index, score = -1, 0.0
            if index >= 0:
                names = self._cfg.keyword_names
                keyword = names[index] if 0 <= index < len(names) else str(index)
                return {
                    "hit": True,
                    "keyword": keyword,
                    "index": index,
                    "score": round(score, 3),
                    "offset_ms": round(len(frames) / 2 / rate * 1000, 1),
                }

        return {"hit": False, "best_score": round(best_score, 3)}

    # ── 状态输出 ──────────────────────────────────────────────────

    def _self_check(self) -> dict[str, Any]:
        """打包体检。

        以往这类问题表现为"点了没反应但也不报错"，排查要花很久。这里把
        「文件到底在不在」直接暴露出来，缺哪个一眼可见。
        """
        paths = self._cfg.keyword_paths
        return {
            "frozen": bool(getattr(sys, "frozen", False)),
            "meipass": getattr(sys, "_MEIPASS", None),
            "model_path": self._cfg.model_path,
            "model_exists": os.path.isfile(self._cfg.model_path),
            "keyword_paths": paths,
            "keyword_paths_exist": [os.path.isfile(p) for p in paths],
            "library_path": self._cfg.library_path,
            "library_exists": os.path.isfile(self._cfg.library_path)
            if self._cfg.library_path
            else False,
            "capture_dir_exists": os.path.isdir(self._capture_dir()),
            "pvporcupine_importable": _can_import("pvporcupine"),
            "pyaudio_importable": _can_import("pyaudio"),
            # --- sherpa 段 ---
            "sherpa_importable": _can_import("sherpa_onnx"),
            "sherpa_model_dir": cfg_sherpa_dir(self._cfg),
            "sherpa_ready": self._cfg.sherpa_ready,
            "sherpa_keywords_src": self._cfg.sherpa_keywords_src,
            "sherpa_keywords_src_exists": os.path.isfile(self._cfg.sherpa_keywords_src)
            if self._cfg.sherpa_keywords_src
            else False,
            "sherpa_threshold": round(self._cfg.sherpa_threshold, 4),
            "sherpa_dll_dirs": _sherpa_dll_dirs(),
            "sherpa_ort_version": _sherpa_ort_version(),
            # 存在即为高危：它是 sherpa / onnxruntime 的 DLL 冲突源头
            "stray_system32_onnxruntime": _stray_system_ort(),
            # --- Vosk 段 ---
            "vosk_importable": _can_import("vosk"),
            "vosk_model": self._cfg.vosk_model,
            "vosk_ready": self._cfg.vosk_ready,
            "vosk_grammar": _vosk_grammar_preview(self._cfg),
        }

    def get_info(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "engine": self._engine.name if self._engine else None,
            "keywords": self._cfg.keyword_names,
            "keyword_paths": self._cfg.keyword_paths,
            "sensitivities": self._cfg.sensitivities,
            "device": self._cfg.device_index,
            # 光有 device 下标说明不了问题：Windows 上大量"打得开却全是静音"
            # 的通道。把设备名和实测电平一并暴露，用户才能在界面上一眼看出
            # 到底是引擎没起来，还是麦克风根本没声音。
            "device_name": self._mic_name,
            "device_resolved": self._mic_index,
            "mic_rms": round(self._mic_rms, 5),
            "mic_silent": bool(self._mic_silent),
            # 「准入条件已满足」，按引擎分别判定：sherpa 不需要任何密钥，
            # 若照 porcupine 的规则返回 False，前端会认定引擎未就绪而**连
            # start 都不调**，表现为「开了开关也没反应」，且不会报任何错。
            "access_key_set": bool(self._cfg.access_key)
            or str(self._cfg.engine).lower() == "sherpa",
            "cooldown_s": self._cfg.cooldown_s,
            "dual_threshold": self._cfg.dual_threshold,
            "allow_energy_fallback": self._cfg.allow_energy_fallback,
            "save_captures": self._cfg.save_captures,
            "peak_confidence": round(self._peak_confidence, 3),
            "peak_level": round(self._peak_level, 3),
            "detections_total": self._detections_total,
            "false_alarms_marked": self._false_alarms_marked,
            "running": bool(self._thread and self._thread.is_alive()),
            "error": self._error,
            "hint": self._hint,
            "self_check": self._self_check(),
        }


def _can_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def cfg_sherpa_dir(cfg: WakeConfig) -> str:
    """当前配置指向的 sherpa 模型目录，用于体检面板定位问题。"""
    for p in (cfg.sherpa_tokens, cfg.sherpa_encoder, cfg.sherpa_decoder, cfg.sherpa_joiner):
        parent = os.path.dirname(p or "")
        if parent and os.path.isdir(parent):
            return parent
    return _discover_sherpa_model_dir()


def _sherpa_dll_dirs() -> list[str]:
    try:
        from . import sherpa_runtime

        return sherpa_runtime.added_dirs()
    except Exception:
        return []


def _sherpa_ort_version() -> str:
    """sherpa 实际使用的 onnxruntime 版本。

    与已加载的 pip onnxruntime 并存时两者版本不同是正常的 —— 只要这份不是
    System32 里那份 1.17.1，就不会触发 API 版本冲突。
    """
    try:
        from . import sherpa_runtime  # noqa: F401

        import sherpa_onnx

        return str(sherpa_onnx.onnxruntime_version)
    except Exception:
        return ""


_STRAY_ORT_PATHS = (
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "onnxruntime.dll"),
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64", "onnxruntime.dll"),
)


def _stray_system_ort() -> bool:
    """系统目录里是否存在游离的 onnxruntime.dll。

    不做删除或改名 —— 那是系统级改动，交给使用者决定。这里只负责把事实
    暴露到体检面板，让"为什么 DLL 加载失败"有据可查。
    """
    return any(os.path.isfile(p) for p in _STRAY_ORT_PATHS)


# 模块底部单例 —— 与项目既有约定一致（参见 app/tools.py、app/plugins/manager.py）
wake_word_service = WakeWordService()
