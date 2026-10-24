"""进程内**唯一**的一条 16k 输入流，供语音录音共用。

为什么不能再"每录一次开一次麦克风"
------------------------------------
旧实现每次 ``/record/start`` 都 ``pa.PyAudio()`` 新建实例 → 打开一条流 →
录完在**后台线程**里 ``terminate()``。单次录音看不出毛病，一到连续对话必然
崩，而且崩得极其隐蔽（本机实测日志）：

    第一轮 record → 14 帧，裁剪到 8 帧 → STT「按系统时区来」✅
    第二轮 record → 24 帧，idle=True    ❌ 整段没有超过说话阈值的帧
    第三轮 record → 20 帧，idle=True    ❌ 同样
    重新喊唤醒词
    第四轮 record → 20 帧，裁剪到 9 帧 → STT「查询湖北荆州的天气」✅
    第五轮 record → 24 帧，idle=True    ❌

注意**帧数是满的**：6.6 秒 ÷ 256ms ≈ 24 帧，完全对得上，说明流是真的在跑、
回调也在一帧不落地触发。可整段音频的 RMS 全在说话阈值以下。这不是"用户没
说话"，是这条流读到的是哑数据。

根因是新流与上一轮尚未执行完的 ``terminate()`` 撞在了一起：两轮之间的间隔
只有 14 毫秒（stop 返回到下一次 start），而 PortAudio 的反初始化要几十毫秒
量级，新旧实例的生命周期因此叠在一起。只有**冷启动**的那一轮是干净的。

所以修法不是"加个 sleep 等它关完" —— 那只是把竞态窗口挪小。这里改成：
麦克风**一次打开、长期运行**，录音只是把自己的监听器挂上去收帧。第一、二、
三轮因此走的是完全相同的代码路径，竞态从结构上被消灭。

另一个不能忘的点（mic_selector.py 里有完整实测数据）：这台机器的阵列麦在
**阻塞 read()** 下会返回削顶 + 直流偏置的假信号，所以这里同样只用回调模式
取数，且回调里禁止做任何可能抛异常或耗时的操作。

生命周期
--------
* 第一次有监听者时懒打开；
* 所有监听者都摘掉之后再空闲 ``IDLE_CLOSE_S`` 秒释放麦克风（笔记本上麦克风
  占用指示灯不该一直亮着）；
* 流开着却超过 ``STARVED_S`` 秒没送来任何一帧 = 流已经死了（常见于设备被
  拔掉或系统回收），下次取用时自动重建。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# 256ms 一帧，与 VAD 的分帧口径保持一致
FRAMES_PER_BUFFER = 4096

# 所有监听者都摘掉之后再等多久关掉麦克风。
# 留这段是为了覆盖连续对话的轮间空档——每轮都冷启动的话，阵列麦的 AGC
# 要重新爬升几百毫秒，头几帧会偏弱。
IDLE_CLOSE_S = 25.0
# 流开着却这么久没来任何一帧 = 流已经死了，下次取用时要重建
STARVED_S = 3.0
_REAPER_INTERVAL_S = 2.0

_lock = threading.RLock()
_listeners: list[Callable[[bytes], None]] = []

_pyaudio_mod: Any = None
_pa: Any = None
_stream: Any = None
_device: int | None = None
_last_frame_ts = 0.0
_last_use_ts = 0.0
_open_error: str | None = None
_reaper_started = False


def _teardown_locked() -> None:
    """关闭当前流并释放 PortAudio。调用方必须已持有 ``_lock``。"""
    global _pa, _pyaudio_mod, _stream, _device
    if _stream is not None:
        try:
            _stream.stop_stream()
        except Exception:
            pass
        try:
            _stream.close()
        except Exception:
            pass
    if _pa is not None:
        try:
            _pa.terminate()
        except Exception:
            pass
    _stream = None
    _pa = None
    _pyaudio_mod = None
    _device = None


def _open_locked(device_index: int | None) -> None:
    """打开共享流。调用方必须已持有 ``_lock``。"""
    global _pyaudio_mod, _pa, _stream, _device, _last_frame_ts, _open_error

    try:
        import pyaudio as pa
    except ImportError as exc:  # pragma: no cover - 环境问题
        _open_error = (
            "pyaudio 未安装，麦克风录音不可用。请在项目 venv 执行："
            "server\\.venv\\Scripts\\python.exe -m pip install pyaudio==0.2.14"
        )
        raise RuntimeError(_open_error) from exc

    _pyaudio_mod = pa
    _pa = pa.PyAudio()

    def _callback(in_data, frame_count, time_info, status):
        global _last_frame_ts
        _last_frame_ts = time.time()
        # PortAudio 的回调线程里不能抛异常（会直接掐断整条流），也不能做
        # 耗时操作（会拖垮所有监听者）。所以:先快照 listener 列表，再逐个
        # 包裹 try —— 一个监听者出错不该影响别人。
        try:
            targets = list(_listeners)
        except Exception:
            targets = []
        for fn in targets:
            try:
                fn(in_data)
            except Exception:
                logger.exception("麦克风帧处理出错")
        return (None, pa.paContinue)

    kwargs: dict[str, Any] = dict(
        format=pa.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
        stream_callback=_callback,
    )

    dev = device_index
    if dev is None:
        # 和唤醒监听用同一条自动挑出来的通道。默认设备常常是「打得开、读
        # 出来全是静音」的死通道，两边各选各的会让人以为功能时好时坏。
        try:
            from .mic_selector import pick_input_device

            dev = pick_input_device().get("index")
        except Exception:
            dev = None
    if dev is not None:
        kwargs["input_device_index"] = dev

    try:
        _stream = _pa.open(**kwargs)
    except Exception:
        # 选中的设备此刻打不开（被别的程序独占 / 刚拔掉）→ 回退系统默认，
        # 不要因为一次选择就把麦克风判死
        kwargs.pop("input_device_index", None)
        dev = None
        _stream = _pa.open(**kwargs)

    _device = dev
    _last_frame_ts = time.time()
    _stream.start_stream()
    _open_error = None
    logger.info(
        "共享麦克风已打开：device=%s rate=%d buffer=%d",
        dev, SAMPLE_RATE, FRAMES_PER_BUFFER,
    )


def _maybe_reap_locked() -> None:
    """按需重建：设备变了或流已经饿死。调用方必须已持有 ``_lock``。"""
    global _last_use_ts
    _last_use_ts = time.time()
    if _stream is None:
        return
    if time.time() - _last_frame_ts > STARVED_S:
        logger.warning(
            "共享流 %.1fs 没有送来任何音频，判定为已死并重建",
            time.time() - _last_frame_ts,
        )
        _teardown_locked()


def ensure(device_index: int | None = None) -> dict[str, Any]:
    """确保共享流处于可用状态。幂等，可以放心高频调用。

    返回诊断信息；打不开时抛 RuntimeError，由调用方转成 HTTP 错误。
    """
    global _last_use_ts
    with _lock:
        _start_reaper_locked()
        _maybe_reap_locked()
        if _stream is None:
            _open_locked(device_index)
        elif device_index is not None and _device != device_index:
            # 用户在设置页换了麦克风：跟着换，不能抱着旧设备不放
            logger.info(
                "麦克风设备从 %s 换成 %s，重建共享流", _device, device_index
            )
            _teardown_locked()
            _open_locked(device_index)
        return stats_locked()


def add_listener(fn: Callable[[bytes], None], device_index: int | None = None) -> Callable[[], None]:
    """挂一个收帧回调，返回用于摘除它的函数（幂等）。

    每帧是在 PortAudio 的回调线程里同步派发的，所以 ``fn`` 必须很快返回
    且不能往外抛异常（外层虽有兜底，但代价是丢掉那一帧的处理结果）。
    """
    ensure(device_index)
    with _lock:
        _listeners.append(fn)
        global _last_use_ts
        _last_use_ts = time.time()

    removed = False

    def _remove() -> None:
        nonlocal removed
        if removed:
            return
        removed = True
        with _lock:
            try:
                _listeners.remove(fn)
            except ValueError:
                pass
            _last_use_ts = time.time()

    return _remove


def stats_locked() -> dict[str, Any]:
    with _lock:
        return {
            "open": _stream is not None,
            "device": _device,
            "listeners": len(_listeners),
            "last_frame_age": (
                round(time.time() - _last_frame_ts, 2) if _last_frame_ts else None
            ),
            "error": _open_error,
        }


def stats() -> dict[str, Any]:
    return stats_locked()


def shutdown() -> None:
    """关闭共享流（测试与停机清理用）。"""
    with _lock:
        _listeners.clear()
        _teardown_locked()


def _start_reaper_locked() -> None:
    """起一个空闲回收线程，麦克风没人听的时候把它关掉。"""
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def _reaper() -> None:
        while True:
            time.sleep(_REAPER_INTERVAL_S)
            try:
                with _lock:
                    if _stream is None:
                        continue
                    if _listeners:
                        continue
                    if time.time() - _last_use_ts < IDLE_CLOSE_S:
                        continue
                    logger.info("麦克风空闲超时，关闭共享流以释放设备")
                    _teardown_locked()
            except Exception:  # pragma: no cover - 回收线程不能先死
                logger.exception("麦克风回收线程异常")

    threading.Thread(target=_reaper, daemon=True, name="jarvis-mic-reaper").start()
