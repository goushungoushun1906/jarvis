"""麦克风自动选择：挑出**真正能收到声音**的那条输入通道。

为什么需要这个模块
------------------
Windows 上「默认输入设备」并不等于「能用的麦克风」。最常见的是英特尔智音
（Intel Smart Sound）阵列：同一块硬件会暴露好几个 endpoint，PyAudio 能把
其中几个正常打开、read() 也不报错，读出来却几乎没有声音（本机实测默认通道
rms 0.003，而另一条**同名**通道 rms 0.59 —— 差两个数量级）。

程序要是老实跟着系统默认走，症状非常有迷惑性：

* 后端日志全绿——引擎起来了、流打开了、电平事件一直在推；
* 前端也没报错——SSE 连着、状态是 listening；
* 但用户怎么喊都不唤醒，按按钮录 20 秒也是「没听清」。

因为送进模型的从头到尾都是静音。这种问题不会以「异常」的形式出现，只会
表现为「改了很多版却一点变化都没有」。所以这里不信任默认值，改为**逐条
探测**后再决定。

判定顺序：先看宿主 API，再看电平
--------------------------------
只比电平是不够的。**安静环境下好通道与死通道读到的都是 ~0.0005 的底噪，
直接比电平等于比谁的本底噪声大**，选出来的完全是随机的——本机实测同一台
机器上一会儿选中 device 5（rms 0.077）、一会儿选中 device 1（rms 0.0005），
而 device 1 是坏的。

真正稳定的判据是**宿主 API**：

| 通道 | API | 回放同一段音频的实测 rms |
|---|---|---|
| 0 声音映射器 | MME | 0.0010（死）|
| 1 麦克风阵列 | MME | 0.0010（死，且是系统默认）|
| 4 主声音捕获驱动程序 | DirectSound | 0.0992（可用）|
| 5 麦克风阵列 | DirectSound | 0.0997（可用）|

所以排序是 WASAPI > DirectSound > WDM-KS > MME。
电平**只在确实听到声音时（≥ ALIVE_RMS）才参与比较**，
安静时一律按 0 处理，把决定权交给 API。
"""

from __future__ import annotations

import logging
import math
import queue
import struct
import time
from typing import Any

logger = logging.getLogger(__name__)

# 低于此值认为这条通道是「死」的（打得开但收不到声音）。
#
# 0.002 是阻塞 read 时代的遗留值：那时测出来的是削顶后的假电平（动辄 0.29），
# 阈值必须跟着虚高。换成回调模式后实测安静房间也有 0.0008~0.005，
# 阈值 0.002 会让**好通道在安静房间里被误判成死通道**，继而触发无谓的换通道。
# 真正被系统静音的通道读到的是 0（连本底噪声都没有），用 0.0005 足够区分。
SILENT_RMS = 0.0005
# 高于此值才认为"确实听到了声音"，电平这才参与通道排序。
# 安静房间的底噪在 0.0008~0.005 之间波动，阈值必须明显高过它，
# 否则排序会被噪声牵着走（选中的是当时本底噪声最大的那条）。
ALIVE_RMS = 0.008
# 每条设备探测多久
PROBE_MS = 600
# 刚打开设备时读到的头几帧常是纯 0（阵列麦要预热/AGC 爬升），直接拿来算
# 电平会把一条好通道误判成死通道，所以先丢掉这一段再开始测量
WARMUP_MS = 300
# 唤醒引擎与 STT 都是 16k，只接受能直接开在 16k 的设备，省去重采样
PROBE_RATE = 16000
# 最多探测几条（防止设备多时启动被拖慢）
MAX_PROBES = 6
# 探测结果缓存多久
CACHE_TTL_S = 300.0

# 一类通道一律跳过：
# 回环/混音设备——抓的是系统播放的声音，选到它会让播报把自己唤醒（自激），
# 比选到静音通道更糟。
#
# 注意「主声音捕获驱动程序 / Primary Sound Capture Driver」**不在**排除名单里。
# 早先按"MME 聚合通道"把它排掉了，但扬声器回放 A/B 实测它 rms 0.0992，
# 与真通道（0.0997）几乎一致 —— 它是能真采集的，排掉等于白扔一条可用通道。
# 真正该排的是「声音映射器 / Sound Mapper」：同批测试里它只有 0.0010，是死的。
_EXCLUDE_TOKENS = (
    "output",
    "输出",
    "混音",
    "stereo mix",
    "what u hear",
    "声音映射器",
    "sound mapper",
)

# 宿主 API 的优先级。数值越小越优先。
#
# 为什么必须看 API 而不只看电平：安静环境下好通道与死通道读到的都是
# ~0.0005 的底噪，直接比电平等于比噪声，会在两条通道之间随机摇摆
# （实测同一台机器上一会儿选到 device 5、一会儿选到 device 1）。
# 而本机实测 MME 这条老式 API 在英特尔智音阵列麦上是坏的：扬声器回放
# 同一段音频，MME 通道只录到 0.001，DirectSound 通道录到 0.0997，差 100 倍。
# 所以 API 才是稳定判据，电平只在真有人说话时才拿来做比较。
_HOST_API_RANK = {
    "wasapi": 0,
    "directsound": 1,
    "wdm-ks": 2,
    "mme": 3,
}


def _host_api_rank(name: str) -> int:
    low = (name or "").lower()
    for key, rank in _HOST_API_RANK.items():
        if key in low:
            return rank
    return 4

_unpack = struct.Struct("<1024h").unpack_from

_CACHE: dict[str, Any] = {"ts": 0.0}
# 已证实收不到声音的设备，短时间内不再试
_DEAD: dict[int, float] = {}
_DEAD_TTL_S = 600.0


def _rms16(data: bytes) -> float:
    """一小段 16bit PCM 的 RMS。长度可能不足一帧，按实际样本算。"""
    n = len(data) // 2
    if n <= 0:
        return 0.0
    try:
        samples = _unpack(data) if n >= 1024 else struct.unpack_from(f"<{n}h", data)
    except struct.error:
        return 0.0
    total = 0.0
    for s in samples:
        v = s / 32768.0
        total += v * v
    return math.sqrt(total / len(samples))


def _is_excluded(name: str) -> bool:
    low = name.lower()
    return any(tok in low or tok in name for tok in _EXCLUDE_TOKENS)


# 「主声音捕获驱动程序」能真采集（回放实测 rms 0.0992，与真通道相当），
# 但它转发的是系统默认设备——默认设备一变，它的输出跟着变。
# 直接命名的阵列麦（如「麦克风阵列 …」）才是稳定的，所以同 API 下让它优先。
_AGGREGATE_TOKENS = ("primary sound capture", "主声音捕获")


def _is_aggregate(name: str) -> bool:
    low = (name or "").lower()
    return any(tok in low or tok in name for tok in _AGGREGATE_TOKENS)


def _is_likely_builtin(name: str) -> bool:
    """猜是不是笔记本内置麦克风。只用于排序与界面提示。"""
    low = name.lower()
    markers = ("内置", "internal", "realtek", "conexant", "麦克风阵列", "microphone array")
    return any(m in name or m in low for m in markers)


def list_input_devices() -> list[dict[str, Any]]:
    """列出可用输入设备。任何异常都不外抛——界面要靠它做诊断。"""
    try:
        import pyaudio as pa
    except Exception:
        return []

    py_audio = None
    devices: list[dict[str, Any]] = []
    try:
        py_audio = pa.PyAudio()
        try:
            default_index = int(py_audio.get_default_input_device_info()["index"])
        except Exception:
            default_index = -1
        for i in range(py_audio.get_device_count()):
            try:
                info = py_audio.get_device_info_by_index(i)
            except Exception:
                continue
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            name = str(info.get("name", ""))
            try:
                host_api = py_audio.get_host_api_info_by_index(int(info["hostApi"]))["name"]
            except Exception:
                host_api = ""
            devices.append(
                {
                    "index": i,
                    "name": name,
                    "max_input_channels": int(info.get("maxInputChannels", 0) or 0),
                    "default_sample_rate": int(info.get("defaultSampleRate", 0) or 0),
                    "host_api": host_api,
                    "is_default": i == default_index,
                    "likely_builtin": _is_likely_builtin(name),
                }
            )
    except Exception as e:  # pragma: no cover - 依赖具体机器
        logger.warning("枚举麦克风失败：%s", e)
    finally:
        if py_audio is not None:
            try:
                py_audio.terminate()
            except Exception:
                pass
    return devices


def probe_device(
    index: int,
    py_audio: Any = None,
    ms: int = PROBE_MS,
) -> dict[str, float] | None:
    """打开设备录一小段，返回实测电平。打不开返回 None。

    只试 16k：唤醒引擎和 STT 都要 16k，开不了 16k 的设备后面也用不上。

    ``py_audio`` 可传入外部创建的实例，用于一次探测多条设备时复用。**不要
    为每条设备各建一个再 terminate** —— PortAudio 反复初始化/反初始化在
    Windows 上不稳定，而这段代码跑在唤醒引擎每次启动的路径上。
    """
    owns = py_audio is None
    if owns:
        try:
            import pyaudio as pa
        except Exception:
            return None
        py_audio = pa.PyAudio()
    try:
        return _measure(py_audio, index, ms)
    finally:
        if owns:
            try:
                py_audio.terminate()
            except Exception:
                pass


def _measure(py_audio, index: int, ms: int) -> dict[str, float] | None:
    """录一小段并统计电平。

    **必须用回调模式取数，不能用阻塞 read()**。同一台机器同一时刻实测
    （英特尔智音阵列麦 device 5）：

        阻塞 read → rms 0.03987  peak 1.0000（削顶，带直流偏置）
        回调     → rms 0.00540  peak 0.0460（干净）

    阻塞读拿回来的信号被 PortAudio 钳位到满刻度，测出来的"电平"是假的。
    探测结果本来就不该被它污染，何况这个模块送出的数据会直接显示在
    诊断面板上——拿削顶值去判断"这条通道有没有声音"，等于自欺欺人。
    """
    try:
        import pyaudio as pa
    except Exception:
        return None

    buf: "queue.Queue[bytes]" = queue.Queue(maxsize=64)

    def _callback(in_data, frame_count, time_info, status):
        try:
            buf.put_nowait(in_data)
        except queue.Full:
            pass
        return (None, pa.paContinue)

    try:
        stream = py_audio.open(
            format=pa.paInt16,
            channels=1,
            rate=PROBE_RATE,
            input=True,
            input_device_index=index,
            frames_per_buffer=1024,
            stream_callback=_callback,
        )
    except Exception:
        return None

    try:
        stream.start_stream()

        # 预热：阵列麦刚打开时读到的是纯 0（AGC 爬升），先丢掉这段
        deadline = time.time() + WARMUP_MS / 1000.0
        while time.time() < deadline:
            try:
                buf.get(timeout=0.05)
            except queue.Empty:
                pass

        frames = max(1, int(PROBE_RATE / 1024 * ms / 1000))
        peak = 0.0
        total = 0.0
        count = 0
        per_frame_s = 1024 / PROBE_RATE
        for _ in range(frames):
            try:
                data = buf.get(timeout=per_frame_s + 0.5)
            except queue.Empty:
                break
            rms = _rms16(data)
            total += rms
            count += 1
            if rms > peak:
                peak = rms
        if count == 0:
            return None
        return {"avg": total / count, "peak": peak}
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass


def mark_dead(index: int | None) -> None:
    """标记某条通道实测无声音，短时间内不再选它。"""
    if index is None:
        return
    _DEAD[index] = time.time()
    if _CACHE.get("index") == index:
        _CACHE.clear()
        _CACHE["ts"] = 0.0
    logger.warning("麦克风通道 %s 实测无声音，已列入黑名单", index)


def reset_cache() -> None:
    _CACHE.clear()
    _CACHE["ts"] = 0.0
    _DEAD.clear()


def _candidate_order(devices: list[dict[str, Any]], preferred: int | None) -> list[dict[str, Any]]:
    now = time.time()
    alive = [d for d in devices if now - _DEAD.get(d["index"], 0.0) > _DEAD_TTL_S]
    usable = [d for d in alive if not _is_excluded(d["name"])]

    def rank(d: dict[str, Any]) -> tuple:
        # API 排在 is_default 前面：系统默认设备恰好是 MME 那条坏通道时，
        # 按默认值排序等于把死通道永远排在第一位。
        return (
            0 if preferred is not None and d["index"] == preferred else 1,
            _host_api_rank(d.get("host_api", "")),
            0 if d["likely_builtin"] else 1,
            0 if d["is_default"] else 1,
            d["index"],
        )

    return sorted(usable or alive, key=rank)


def pick_input_device(preferred: int | None = None, force: bool = False) -> dict[str, Any]:
    """挑一条真正有声音的输入通道。

    返回 ``{"index", "name", "rms", "silent", "probed"}``。
    ``index`` 为 None 表示一条都没探到（多半是系统层面把麦克风挡了），
    调用方此时应退回系统默认并明确告诉用户「麦克风没有声音」。
    """
    now = time.time()
    if not force and _CACHE.get("ts") and now - _CACHE["ts"] < CACHE_TTL_S:
        cached = {k: _CACHE[k] for k in ("index", "name", "rms", "silent", "probed") if k in _CACHE}
        if preferred is not None and cached.get("index") != preferred:
            pass  # 用户显式指定设备时不走缓存
        else:
            return cached

    devices = list_input_devices()
    if not devices:
        return {"index": None, "name": None, "rms": 0.0, "silent": True, "probed": []}

    ordered = _candidate_order(devices, preferred)
    probed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    # 整轮探测共用一个 PortAudio 实例：反复 initialize/terminate 在 Windows
    # 上会崩，而这里最多要开合 6 条设备。
    py_audio = None
    try:
        import pyaudio as pa

        py_audio = pa.PyAudio()
    except Exception:
        py_audio = None

    try:
        for dev in ordered[:MAX_PROBES]:
            result = probe_device(dev["index"], py_audio)
            if result is None:
                continue
            entry = {
                "index": dev["index"],
                "name": dev["name"],
                "avg": round(result["avg"], 5),
                "peak": round(result["peak"], 5),
                "is_default": dev["is_default"],
                "api_rank": _host_api_rank(dev.get("host_api", "")),
                "aggregate": _is_aggregate(dev.get("name", "")),
            }
            probed.append(entry)
            candidates.append(entry)

            # 用户显式指定的那条：只要确实能收到声（哪怕偏弱）就用它，
            # 不拿别的通道的电平去压过他的选择。
            if preferred is not None and dev["index"] == preferred and result["avg"] >= SILENT_RMS:
                best = {"index": dev["index"], "name": dev["name"], "avg": result["avg"]}
                break
    finally:
        if py_audio is not None:
            try:
                py_audio.terminate()
            except Exception:
                pass

    if best is None:
        # 必须**全部探完再比**。曾经在这里加过「默认设备够响就直接用」的
        # 快速路径，结果本机默认通道底噪在 0.0008 ~ 0.006 之间波动、刚好能
        # 越过阈值，于是死通道被选中，真正能用的那条（0.59）被完全忽略。
        #
        # 排序键是「宿主 API 优先、电平次之」，而且电平**只有确实听到声音时
        # 才参与比较**。安静房间里好通道与死通道读到的都是 ~0.0005 的底噪，
        # 拿它直接比等于比谁的本底噪声大，选出来的完全是随机的。
        def _score(e: dict[str, Any]) -> tuple:
            level = e["avg"] if e["avg"] >= ALIVE_RMS else 0.0
            return (
                e["api_rank"],
                0 if not e["aggregate"] else 1,  # 转发型的排在直接命名的后面
                -level,
                e["index"],
            )

        candidates.sort(key=_score)
        if not candidates:
            out = {"index": None, "name": None, "rms": 0.0, "silent": True, "probed": probed}
            _CACHE.update(out)
            _CACHE["ts"] = now
            return out
        top = candidates[0]
        best = {"index": top["index"], "name": top["name"], "avg": top["avg"]}

    silent = best["avg"] < SILENT_RMS
    default_index = next((d["index"] for d in devices if d["is_default"]), None)
    default_name = next((d["name"] for d in devices if d["is_default"]), None) if default_index is not None else None
    if best["avg"] < ALIVE_RMS:
        logger.warning(
            "麦克风电平异常低：选到 device=%s（rms=%.5f）。系统默认是 device=%s。"
            "若确实没声音，多半是系统隐私设置挡了麦克风或输入音量为 0",
            best["index"],
            best["avg"],
            default_index,
        )
    elif default_index is not None and best["index"] != default_index:
        logger.info(
            "未采用系统默认输入设备 device=%s，改用 device=%s（rms=%.5f）",
            default_index,
            best["index"],
            best["avg"],
        )

    out = {
        "index": best["index"],
        "name": best["name"],
        "rms": round(best["avg"], 5),
        "silent": silent,
        "probed": probed,
    }
    _CACHE.update(out)
    _CACHE["ts"] = now
    return out


def resolve_device_index(preferred: int | None = None) -> int | None:
    """只要设备下标的便捷入口。"""
    return pick_input_device(preferred)["index"]
