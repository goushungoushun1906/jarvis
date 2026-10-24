"""共享麦克风与 VAD 状态机。

这组测试守的是**连续对话第二轮开始就录不到声音**那个 bug。它的表象极具
迷惑性：后端日志里 record/start、record/stop、VAD 阈值标定、STT 全都正常
返回 200，帧数也是满的（6.6 秒 ÷ 256ms ≈ 24 帧），可第二轮之后的整段音频
RMS 全在说话阈值以下，识别结果永远是 idle。

根因是旧实现每次录音都新建 PyAudio 实例并在后台线程 terminate()，两轮之间
只隔 14 毫秒，新旧实例的生命期叠在一起，新流读到的是哑数据。

所以这里的核心断言不是"能录到音"，而是：

1. 连录三轮，**麦克风从头到尾只被 open 过一次**（如果哪天又变成每轮一条
   流，这个断言会立刻失败，而不是等到用户在真机上喊第二句才崩）；
2. 后两轮**必须和第一轮一样**收到同等强度的音频。
"""

from __future__ import annotations

import os
import sys
import time
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient

_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

os.environ.setdefault("JARVIS_MODE", "dev")

from main import app as _app  # noqa: E402
from app import mic_capture  # noqa: E402
from app.routes import voice as voice_module  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(_app, base_url="http://127.0.0.1:18201") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_mic():
    """每个用例前后都把共享流拆干净，避免用例之间互相污染。

    这条不是可选的卫生习惯：mic_capture 是**模块级单例**，上一个用例留下
    的假 PyAudio 实例会让下一个用例直接复用它，测试之间开始耦合。
    """
    mic_capture.shutdown()
    yield
    mic_capture.shutdown()


def _pcm(rms: float, seconds: float = 0.25) -> bytes:
    """生成指定音量的一段 16-bit PCM（长度对齐到一帧）。"""
    n = int(16000 * seconds)
    wave = np.sin(np.linspace(0, 100 * seconds, n)).astype(np.float32) * rms
    return (wave * 32767).astype("<i2").tobytes()


class _FakeStream:
    def __init__(self, callback):
        self._callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def emit(self, data: bytes):
        self._callback(data, len(data) // 2, None, 0)


class _FakePyAudio:
    """替身 PortAudio：只记录自己被 open/terminate 了几次。"""

    instances: list["_FakePyAudio"]

    def __init__(self):
        # 类变量而非模块变量：这样 suite 跑完还能回头查总数
        type(self).instances.append(self)
        self.streams: list[_FakeStream] = []
        self.terminated = False
        self.open_count = 0

    def open(self, **kwargs):
        self.open_count += 1
        stream = _FakeStream(kwargs["stream_callback"])
        self.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


_FakePyAudio.instances = []


@pytest.fixture()
def fake_audio(monkeypatch):
    """把 mic_capture 的 pyaudio 换成可控替身。"""
    _FakePyAudio.instances = []
    mod = types.ModuleType("pyaudio")
    mod.PyAudio = _FakePyAudio
    mod.paInt16 = 8
    mod.paContinue = 0
    monkeypatch.setitem(sys.modules, "pyaudio", mod)

    # 别让它去探真机：测试机上通常没有麦克风，而且pick_input_device 会
    # 真去 open 每一条设备，把一轮测试拖到十几秒。
    fake_pick = {"index": 0, "name": "fake-mic", "rms": 0.01, "silent": False}
    monkeypatch.setattr(
        "app.mic_selector.pick_input_device", lambda *a, **k: dict(fake_pick)
    )
    opened: list[_FakeStream] = []

    def _emit_all(data: bytes) -> None:
        for inst in _FakePyAudio.instances:
            for stream in inst.streams:
                if stream is not None and stream.started and not stream.closed:
                    stream.emit(data)

    class Handle:
        @property
        def instances(self):
            return _FakePyAudio.instances

        @property
        def total_opens(self):
            return sum(i.open_count for i in _FakePyAudio.instances)

        def emit(self, data: bytes) -> None:
            _emit_all(data)

        @property
        def live_streams(self):
            out = []
            for inst in _FakePyAudio.instances:
                out.extend(s for s in inst.streams if s.started and not s.closed)
            return out

    return Handle()


# ── 共享流的生命周期 ────────────────────────────────────────────────

def test_listener_receives_frames(fake_audio):
    got: list[bytes] = []
    remove = mic_capture.add_listener(got.append, device_index=0)
    fake_audio.emit(b"abc")
    assert got == [b"abc"]
    remove()


def test_remove_listener_is_idempotent(fake_audio):
    got: list[bytes] = []
    remove = mic_capture.add_listener(got.append, device_index=0)
    remove()
    remove()  # 再调一次不能抛，也不能误删别的监听者
    other: list[bytes] = []
    remove2 = mic_capture.add_listener(other.append, device_index=0)
    fake_audio.emit(b"x")
    assert other == [b"x"]
    assert got == []
    remove2()


def test_stream_is_opened_once_for_many_listeners(fake_audio):
    remove_a = mic_capture.add_listener(lambda d: None, device_index=0)
    remove_b = mic_capture.add_listener(lambda d: None, device_index=0)
    assert fake_audio.total_opens == 1
    remove_a()
    remove_b()


def test_one_listener_error_does_not_kill_others(fake_audio):
    def _boom(_data):
        raise RuntimeError("帧处理炸了")

    good: list[bytes] = []
    remove_bad = mic_capture.add_listener(_boom, device_index=0)
    remove_good = mic_capture.add_listener(good.append, device_index=0)
    fake_audio.emit(b"ok")
    assert good == [b"ok"], "某个监听者抛异常不能连累别人"
    remove_bad()
    remove_good()


# ── 连续对话回归：这才是核心 ─────────────────────────────────────────

def test_consecutive_recordings_all_receive_audio(client, fake_audio, monkeypatch):
    """连录三轮：麦克风只开一次，后两轮必须和第一轮一样收到声音。

    这个用例直接对应"第二轮开始整段 idle"的线上故障。旧实现在这里会表现为
    第二、三轮的 level 低于 _SILENCE_RMS（共享流读到哑数据），同时
    total_opens 会是 3。
    """
    monkeypatch.setattr("app.stt.transcribe_wav", lambda _wav: "测试指令", raising=False)

    seen_levels: list[float] = []
    for _round in range(3):
        start = client.post(
            "/api/voice/record/start",
            json={"vad": True, "leading_ms": 2000, "silence_ms": 300},
        )
        assert start.status_code == 200, start.text
        assert start.json()["status"] == "recording"

        # 喂 3 帧明显人声 + 3 帧静音（让人声能被裁剪逻辑识别出来）
        for _ in range(3):
            fake_audio.emit(_pcm(0.05, 0.25))
        for _ in range(3):
            fake_audio.emit(b"\x00" * 8192)

        stop = client.post("/api/voice/record/stop", json={"immediate": True})
        assert stop.status_code == 200, stop.text
        body = stop.json()
        seen_levels.append(body.get("level"))

    # ① 三轮都必须真的收到了声音
    assert len(seen_levels) == 3
    for i, level in enumerate(seen_levels):
        assert level is not None, f"第 {i + 1} 轮压根没走到 STT 分支"
        assert level >= voice_module._SILENCE_RMS, (
            f"第 {i + 1} 轮录到的是静音（level={level}）——这正是连续对话的线上故障"
        )

    # ② 麦克风全程只被打开过一次：标志着"每轮一条流"的写法没有回来
    assert fake_audio.total_opens == 1, (
        f"共享流被重复打开了 {fake_audio.total_opens} 次；"
        "每轮一条 PyAudio 实例会让后续轮次读到哑数据"
    )


def test_recording_detaches_listener_on_stop(client, fake_audio, monkeypatch):
    """stop 之后不能还在收帧，否则下一轮会录进重复的音频。"""
    monkeypatch.setattr("app.stt.transcribe_wav", lambda _wav: "x", raising=False)

    client.post("/api/voice/record/start", json={"vad": True, "leading_ms": 1000})
    client.post("/api/voice/record/stop", json={"immediate": True})
    assert mic_capture.stats()["listeners"] == 0, "监听者没有摘干净"

    # 第二轮开始时如果有残留监听者，帧会被两遍处理 → 文本重复
    client.post("/api/voice/record/start", json={"vad": True, "leading_ms": 1000})
    assert mic_capture.stats()["listeners"] == 1
    client.post("/api/voice/record/stop", json={"immediate": True})


def test_start_recording_never_opens_two_streams(client, fake_audio, monkeypatch):
    """旧实现的致命写法：一次 start 里对同一设备 open 了两条流，第一条被丢弃。

    那条"试试能不能打开"的流从不关闭，等于悄悄占着设备。这里锁死：
    一次 start 只允许存在一条活跃流。
    """
    monkeypatch.setattr("app.stt.transcribe_wav", lambda _wav: "x", raising=False)
    client.post("/api/voice/record/start", json={"vad": True, "leading_ms": 1000})
    assert len(fake_audio.live_streams) == 1
    client.post("/api/voice/record/stop", json={"immediate": True})


# ── VAD 状态机 ──────────────────────────────────────────────────────

def _collector(**overrides) -> voice_module.VadCollector:
    params = voice_module.RecordStartRequest(**overrides)
    return voice_module.VadCollector(params)


def test_vad_idle_when_nobody_speaks_within_leading():
    """等满 leading 也没人开口 → idle，不算故障。"""
    collector = _collector(vad=True, leading_ms=600)
    for _ in range(6):
        collector.consume(b"\x00" * 8192)
    collector.started_at = time.time() - 5  # 假装已经等了 5 秒
    collector.consume(b"\x00" * 8192)
    assert collector.finished is True
    assert collector.idle is True


def test_vad_finishes_after_speech_then_silence():
    """说完话 + 静音够长 → 判停，且不是 idle。"""
    collector = _collector(vad=True, leading_ms=2000, silence_ms=300, min_ms=200)
    for _ in range(3):
        collector.consume(b"\x00" * 8192)      # 先标定底噪
    for _ in range(3):
        collector.consume(_pcm(0.05, 0.25))    # 说话
    assert collector.speech_seen is True
    for _ in range(3):
        collector.consume(b"\x00" * 8192)      # 说完，静音
    assert collector.finished is True
    assert collector.idle is False


def test_calibration_is_not_poisoned_when_user_starts_talking_immediately():
    """用户在录音第一帧就开口时，不能把人声当成环境底噪。

    这是比"第二轮变哑"更常见的一种失败：连续对话里用户常常是上一句播完
    立刻接话，此时开头几帧就是人声。若照中位数估底噪，门槛会被抬到人声
    的 3 倍，用户整句话全部判为"没到说话门槛" —— 界面上的表现同样是
    「聆听着就没下文」，而且日志看起来一切正常。
    """
    collector = _collector(vad=True, leading_ms=2000, silence_ms=300, min_ms=200)
    # 一上来就是人声（不给任何安静帧）
    for _ in range(3):
        collector.consume(_pcm(0.05, 0.25))
    # 门槛必须仍是默认下限，而不是人声的 3 倍
    assert collector.speech_thr <= voice_module._VAD_SPEECH_RMS_FLOOR, (
        f"门槛被标定成 {collector.speech_thr}，比人声还高，整句都会被判静音"
    )
    assert collector.speech_seen is True


def test_calibration_still_uses_quiet_room_noise():
    """安静环境里仍然按底噪推导，不能一律退回默认下限。"""
    collector = _collector(vad=True, leading_ms=2000)
    for _ in range(3):
        collector.consume(_pcm(0.0015, 0.25))
    # 底噪极低时 speech 应该落在下限上，silence 也应是最小值附近的推导结果
    assert collector.speech_thr == voice_module._VAD_SPEECH_RMS_FLOOR
    assert 0 < collector.silence_thr < collector.speech_thr


def test_vad_does_not_finish_before_calibration():
    """阈值还没标定时一律不判停，否则第一条 sentence 会被瞬间截断。"""
    collector = _collector(vad=True, leading_ms=2000)
    collector.consume(_pcm(0.05, 0.25))
    collector.consume(b"\x00" * 8192)
    collector.consume(b"\x00" * 8192)
    assert collector.finished is False


def test_vad_trim_result_matches_speech():
    collector = _collector(vad=True, leading_ms=2000)
    for _ in range(3):
        collector.consume(b"\x00" * 8192)
    speech = _pcm(0.05, 0.25)
    collector.consume(speech)
    for _ in range(4):
        collector.consume(b"\x00" * 8192)
    frames, _ = collector.trimmed()
    # 人声那一帧必须在裁剪结果里
    assert any(f == speech for f in frames)


def test_all_silence_trims_to_nothing():
    collector = _collector(vad=True, leading_ms=2000)
    for _ in range(8):
        collector.consume(b"\x00" * 8192)
    frames, _ = collector.trimmed()
    assert frames == []
