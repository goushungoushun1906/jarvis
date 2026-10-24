"""录音反馈：失败必须说清楚是"没声音"还是"没听清"。

这一组测试要锁住的行为是：录音结束后如果拿不到文本，接口必须带上
error 说明原因。此前只返回空 text，前端又直接把它当"什么都没发生"，
用户于是只能看到"点了一下麦克风，界面毫无反应"——无法判断是系统没
放行麦克风、说得太短，还是本地模型没打进包。
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

# 与 test_api.py 保持一致：把 server/ 加进 sys.path，否则单独跑这个文件时
# 会出现 "No module named app.main" 这类和真实代码无关的导入失败。
_server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _server_root not in sys.path:
    sys.path.insert(0, _server_root)

os.environ.setdefault("JARVIS_MODE", "dev")

from main import app as _app  # noqa: E402
from app.routes import voice as voice_module  # noqa: E402


@pytest.fixture()
def client():
    # 后端有 Host 白名单守卫，必须显式指回 loopback，否则全部 403。
    with TestClient(_app, base_url="http://127.0.0.1:18200") as c:
        yield c


def _pcm(rms: float, seconds: float = 1.0) -> bytes:
    """生成指定音量的 16-bit PCM 数据块。"""
    n = int(16000 * seconds)
    wave = np.sin(np.linspace(0, 100 * seconds, n)).astype(np.float32) * rms
    return (wave * 32767).astype("<i2").tobytes()


def _seed_recording(monkeypatch, frames: list[bytes], levels: list[float] | None = None, **extra):
    """把录音状态伪造成"正在录，且已经攒下这些帧"。

    active 必须是 True：接口第一步就校验它，否则直接 400 Not recording，
    根本走不到后面的音量判断。

    levels 是每帧 RMS，VAD 模式下 stop 会据此裁剪首尾静音。默认给"每帧都
    有人声"的电平（0.05），与 frames 里的有声音频对应；测静音场景时显式
    传 levels=[0.0]*n。
    """
    state = {
        "active": True,
        "frames": frames,
        "start_time": time.time(),
        "vad": False,
        "idle": False,
        "speech_seen": False,
        "levels": levels if levels is not None else [0.05] * len(frames),
        "silence_thr": voice_module._VAD_SILENCE_RMS_FLOOR,
        "speech_thr": voice_module._VAD_SPEECH_RMS_FLOOR,
        **extra,
    }
    monkeypatch.setattr(voice_module, "_recording_state", state)


# ── 音量检测 ──────────────────────────────────────────────────────────

def test_rms_zero_for_silence():
    assert voice_module._rms_of_raw_pcm(b"\x00" * 32000) == 0.0


def test_rms_above_threshold_for_speech():
    assert voice_module._rms_of_raw_pcm(_pcm(0.05)) >= voice_module._SILENCE_RMS


def test_rms_below_threshold_for_quiet_room():
    # 环境底噪量级，必须判为"没声音"，否则静音也会被送去跑识别
    assert voice_module._rms_of_raw_pcm(_pcm(0.0005)) < voice_module._SILENCE_RMS


def test_level_payload_is_capped_at_100():
    assert voice_module._level_payload(1.0)["level_pct"] == 100


# ── /record/stop 的三种失败 ───────────────────────────────────────────

def test_silent_recording_reports_mic_error(client, monkeypatch):
    _seed_recording(monkeypatch, [b"\x00" * 8192] * 8)
    resp = client.post("/api/voice/record/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    assert "麦克风" in body["error"]
    assert body["level"] < voice_module._SILENCE_RMS


def test_too_short_recording_reports_error(client, monkeypatch):
    _seed_recording(monkeypatch, [b"\x00" * 8192])
    resp = client.post("/api/voice/record/stop")
    assert resp.status_code == 200
    assert "太短" in resp.json()["error"]


def test_audible_but_unrecognized_reports_unclear(client, monkeypatch):
    _seed_recording(monkeypatch, [_pcm(0.05, 0.5)] * 4)
    monkeypatch.setattr(
        "app.stt.transcribe_wav", lambda _wav: "", raising=False
    )
    resp = client.post("/api/voice/record/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    # 与"麦克风没声音"必须是不同文案，否则用户不知道该查权限还是重说
    assert "没听清" in body["error"]
    assert body["level"] >= voice_module._SILENCE_RMS


def test_successful_transcription_has_no_error(client, monkeypatch):
    _seed_recording(monkeypatch, [_pcm(0.05, 0.5)] * 4)
    monkeypatch.setattr(
        "app.stt.transcribe_wav", lambda _wav: "你好贾维斯", raising=False
    )
    resp = client.post("/api/voice/record/stop")
    body = resp.json()
    assert body["text"] == "你好贾维斯"
    assert "error" not in body


# ── mic-check ─────────────────────────────────────────────────────────

def test_mic_check_reports_heard_sound(client, monkeypatch):
    monkeypatch.setattr(
        voice_module, "_record_blocking", lambda s: (_pcm(0.05, s), {"name": "测试麦克风"})
    )
    resp = client.post("/api/voice/mic-check?seconds=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["heard_sound"] is True
    assert body["device"]["name"] == "测试麦克风"
    assert "model_ready" in body


def test_mic_check_reports_silence(client, monkeypatch):
    monkeypatch.setattr(
        voice_module, "_record_blocking", lambda s: (b"\x00" * 32000, {"name": "测试麦克风"})
    )
    body = client.post("/api/voice/mic-check?seconds=1").json()
    assert body["heard_sound"] is False


def test_mic_check_rejects_while_recording(client, monkeypatch):
    monkeypatch.setitem(voice_module._recording_state, "active", True)
    assert client.post("/api/voice/mic-check").status_code == 409


# ── VAD 模式：stop 的语义从"立即停"变成"等说完再转写" ────────────────

def test_vad_idle_returns_quiet_not_error(client, monkeypatch):
    """VAD 等满 leading 也没人开口：返回 idle=true，不带吓人的 error。

    这是连续对话的正常空闲退出路径；若错报成"麦克风故障"，前端会话
    循环会把正常结束渲染成事故。
    """
    _seed_recording(
        monkeypatch,
        [b"\x00" * 8192] * 4,
        vad=True, active=False, idle=True,
    )
    resp = client.post("/api/voice/record/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    assert body["idle"] is True
    assert "error" not in body


def test_vad_already_stopped_transcribes_normally(client, monkeypatch):
    """VAD 判停发生在录音线程里；stop 被调时 active 已是 False，
    必须照常转写已攒下的帧，而不是 400 Not recording。"""
    _seed_recording(
        monkeypatch,
        [_pcm(0.05, 0.5)] * 4,
        vad=True, active=False,
    )
    monkeypatch.setattr(
        "app.stt.transcribe_wav", lambda _wav: "今天天气怎么样", raising=False
    )
    body = client.post("/api/voice/record/stop").json()
    assert body["text"] == "今天天气怎么样"


def test_vad_manual_stop_still_works(client, monkeypatch):
    """手动按停（active=True 时调 stop + immediate）在 VAD 模式下也要能用：
    立即置停并转写，而不是等满 10 秒。"""
    _seed_recording(
        monkeypatch,
        [_pcm(0.05, 0.5)] * 4,
        vad=True, active=True,
    )
    monkeypatch.setattr(
        "app.stt.transcribe_wav", lambda _wav: "手动停止", raising=False
    )
    import time as _t
    t0 = _t.time()
    body = client.post(
        "/api/voice/record/stop", json={"immediate": True}
    ).json()
    assert _t.time() - t0 < 3
    assert body["text"] == "手动停止"


# ── VAD 动态阈值与端点裁剪 ────────────────────────────────────────────
#
# 这两个都是真机实测才暴露的坑：
#   1. 本机安静环境底噪 RMS 0.00496，固定阈值 0.006 几乎贴着它 → VAD 认为
#      "一直在说话"，录满上限才停；
#   2. 大段静音送进识别模型会产生幻觉文本（Paraformer 实测输出过一整句
#      无关的话），接口照样返回 200，只有跑一次才知道错了。

def test_vad_thresholds_scale_with_noise_floor():
    """底噪高的环境阈值必须跟着抬高，否则永远在"说话"。"""
    speech, silence = voice_module._vad_thresholds([0.005] * 8)
    assert speech >= voice_module._VAD_SPEECH_RMS_FLOOR
    assert speech > silence, "要有迟滞，否则一进说话态就出不来"


def test_vad_thresholds_have_floor_on_very_quiet_mic():
    """底噪极低时也不能把阈值降到 0，否则一个喷麦音就算说话。"""
    speech, silence = voice_module._vad_thresholds([0.0001] * 8)
    assert speech >= voice_module._VAD_SPEECH_RMS_FLOOR
    assert silence >= voice_module._VAD_SILENCE_RMS_FLOOR * 0.5


def test_trim_to_speech_drops_leading_and_trailing_silence():
    frames = [b"\x00" * 8192] * 10
    frames[4:6] = [_pcm(0.05, 0.25)] * 2
    levels = [0.001] * 10
    levels[4:6] = [0.05, 0.05]
    out, _ = voice_module._trim_to_speech(frames, levels, 0.006)
    assert 0 < len(out) < len(frames), "首尾静音必须裁掉"
    # 裁剪后仍要含有人声那两帧
    assert _pcm(0.05, 0.25) in out


def test_trim_to_speech_all_silence_returns_empty():
    frames = [b"\x00" * 8192] * 8
    levels = [0.001] * 8
    out, _ = voice_module._trim_to_speech(frames, levels, 0.006)
    assert out == []


def test_vad_all_silence_is_idle_not_hallucination(client, monkeypatch):
    """整段都没人声时必须判 idle 并直接返回，绝不送去识别。"""
    _seed_recording(
        monkeypatch,
        [b"\x00" * 8192] * 8,
        levels=[0.002] * 8,
        vad=True, active=False,
    )
    body = client.post("/api/voice/record/stop").json()
    assert body["text"] == ""
    assert body["idle"] is True
