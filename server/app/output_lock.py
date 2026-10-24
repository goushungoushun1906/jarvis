"""全局「正在出声」锁。

为什么要有这个模块
------------------
贾维斯的麦克风离扬声器只有十几厘米，播报时自己说的话会被自己的麦克风
完整录进去。这件事有三个后果，缺一个防线都会复发：

1. **自激**：唤醒引擎把播报内容听成唤醒词 → 打断自己 → 再播 → 再听，
   死循环。
2. **串音**：连续对话在「回答还没播完」的空档就开录，录到的是自己的
   声音，识别出来一堆胡话，连续几轮 idle 后退不出会话。
3. **延迟**：上面两者叠加后，用户感觉「它搞不清是在思考还是在收音」。

前端当然能维护播放状态，但浏览器那边的时序有天然漏洞：TTS 字节到了
才置 isPlaying=true，而**合成请求发出到真正出声**之间有 1~2 秒空档
（实测日志：LLM 流完 → 合成 1.8s → 才播）。这段空档里前端认为"没在播"，
于是开始收音，接着播报的声音就灌进来了。

所以这里做一个**服务端**的权威标记：TTS 一发出就锁住，锁的时长按音频
实际时长加余量算，唤醒引擎和录音接口都看这个锁。前端状态依然是主防线
（响应快），这把锁是兜底（不怕前端时序漏）。

用法
----
    mark_output(seconds)   # TTS 发出音频后调用
    is_output_active()     # 唤醒引擎 / 录音接口查询
    clear_output()         # 用户手动打断播报时立刻解锁
"""

import threading
import time

# 播报结束到麦克风里的回声散干净还要一会儿（扬声器外壳传导 + 房间反射）。
# 笔记本内置麦离扬声器特别近，余量给足一点。
_TAIL_SECONDS = 0.8

_lock = threading.Lock()
_until = 0.0


def mark_output(seconds: float) -> None:
    """标记"接下来会有这么多秒在出声"。

    传实际音频时长；估算不出来时按文本长度估一个下限（见 voice.py）。
    """
    global _until
    if seconds <= 0:
        return
    with _lock:
        _until = max(_until, time.time() + seconds + _TAIL_SECONDS)


def extend_output(seconds: float) -> None:
    """分句播报时逐句续期（每句播完再续下一句的长度）。"""
    mark_output(seconds)


def clear_output() -> None:
    """立刻解锁——用户手动打断播报时用，否则要白等整段时长。"""
    global _until
    with _lock:
        _until = 0.0


def is_output_active() -> bool:
    with _lock:
        return time.time() < _until


def output_remaining() -> float:
    """还要锁多久（秒）。锁已释放时返回 0。"""
    with _lock:
        return max(0.0, _until - time.time())
