"""麦克风自动选择的测试。

这些用例守的是一个很隐蔽的回归：Windows 上「默认输入设备」经常是一条打得
开、read() 也不报错，但读出来全是数字静音的通道（英特尔智音阵列尤其典型）。
程序只要老实跟着系统默认走，症状就是「功能全对、日志全绿、怎么喊都不醒」。
"""

from __future__ import annotations

import pytest

from app import mic_selector


@pytest.fixture(autouse=True)
def _clean_cache():
    mic_selector.reset_cache()
    yield
    mic_selector.reset_cache()


def _dev(index, name, *, is_default=False, builtin=True, host_api="MME"):
    return {
        "index": index,
        "name": name,
        "is_default": is_default,
        "likely_builtin": builtin,
        "max_input_channels": 2,
        "default_sample_rate": 44100,
        "host_api": host_api,
    }


def _stub(monkeypatch, devices, levels):
    monkeypatch.setattr(mic_selector, "list_input_devices", lambda: devices)
    monkeypatch.setattr(
        mic_selector,
        "probe_device",
        lambda index, ms=mic_selector.PROBE_MS: (
            {"avg": levels[index], "peak": levels[index] * 2} if index in levels else None
        ),
    )


def test_default_channel_silent_switches_to_live_one(monkeypatch):
    """系统默认那条是死通道时，必须改选真正有声音的那条。"""
    devices = [_dev(1, "麦克风阵列 (Intel)", is_default=True), _dev(5, "麦克风阵列 (Intel)")]
    _stub(monkeypatch, devices, {1: 0.0008, 5: 0.02})

    pick = mic_selector.pick_input_device(force=True)

    assert pick["index"] == 5
    assert pick["silent"] is False
    assert pick["rms"] == pytest.approx(0.02, abs=1e-6)


def test_all_candidates_are_probed_before_choosing(monkeypatch):
    """候选必须**全探完**再比，不能默认设备够响就提前收工。

    反例就是本机：默认通道底噪波动（0.0008 ~ 0.006）刚好越过"够响"线，
    于是死通道被选中，真正能用的通道（0.59）被跳过。
    """
    devices = [_dev(1, "麦克风阵列 (Intel)", is_default=True), _dev(5, "麦克风阵列 (Intel)")]
    _stub(monkeypatch, devices, {1: 0.03, 5: 0.02})

    pick = mic_selector.pick_input_device(force=True)

    assert pick["index"] == 1  # 仍然选电平最高的那条
    assert len(pick["probed"]) == 2


def test_louder_channel_beats_silent_default(monkeypatch):
    """默认通道"能打开但几乎没声"时，必须让位给真正有信号的通道。"""
    devices = [_dev(1, "麦克风阵列 (Intel)", is_default=True), _dev(5, "麦克风阵列 (Intel)")]
    _stub(monkeypatch, devices, {1: 0.004, 5: 0.59})

    pick = mic_selector.pick_input_device(force=True)

    assert pick["index"] == 5
    assert pick["silent"] is False


def test_loopback_device_is_never_picked(monkeypatch):
    """回环设备（抓的是系统播放声）比静音通道更糟，必须排除。"""
    devices = [
        _dev(1, "麦克风阵列 (Intel)", is_default=True),
        _dev(12, "Input (Senary Audio output)", builtin=False),
        _dev(5, "麦克风阵列 (Intel)"),
    ]
    _stub(monkeypatch, devices, {1: 0.0005, 12: 0.9, 5: 0.02})

    pick = mic_selector.pick_input_device(force=True)

    assert pick["index"] == 5
    assert all(12 != p["index"] for p in pick["probed"])


def test_all_channels_silent_reports_silent(monkeypatch):
    """一条都收不到声音时不能假装正常——要让用户看到「麦克风没声音」。"""
    devices = [
        _dev(1, "麦克风阵列 (Intel)", is_default=True, host_api="MME"),
        _dev(5, "麦克风阵列 (Intel)", host_api="Windows DirectSound"),
    ]
    # 真正被系统静音的通道读到的是 0 附近（连本底噪声都没有）
    _stub(monkeypatch, devices, {1: 0.00002, 5: 0.00003})

    pick = mic_selector.pick_input_device(force=True)

    # 全是底噪时电平不参与比较（比的是噪声不是信号），由宿主 API 决定
    assert pick["index"] == 5
    assert pick["silent"] is True


def test_quiet_room_is_not_mistaken_for_dead(monkeypatch):
    """安静房间不能被误判成麦克风静音。

    安静房间的真实底噪实测 0.0008~0.005。早先阈值是 0.002，
    会让好通道在安静房间里被判成死通道，进而触发无谓的换通道。
    """
    devices = [
        _dev(1, "麦克风阵列 (Intel)", is_default=True, host_api="MME"),
        _dev(5, "麦克风阵列 (Intel)", host_api="Windows DirectSound"),
    ]
    _stub(monkeypatch, devices, {1: 0.0022, 5: 0.0038})

    pick = mic_selector.pick_input_device(force=True)
    assert pick["index"] == 5
    assert pick["silent"] is False


def test_dead_mme_channel_with_higher_noise_loses(monkeypatch):
    """安静时死通道的底噪可能略高，不能因此选中它。

    本机真实案例：MME 通道测到 0.00049、DirectSound 通道只有 0.00042，
    按"取电平最高"会选到 MME —— 而 MME 这条实测是坏的：扬声器回放同一段
    音频，它只录到 0.001，DirectSound 录到 0.0997，差 100 倍。
    """
    devices = [
        _dev(1, "麦克风阵列 (Intel)", is_default=True, host_api="MME"),
        _dev(5, "麦克风阵列 (Intel)", host_api="Windows DirectSound"),
    ]
    _stub(monkeypatch, devices, {1: 0.00049, 5: 0.00042})

    pick = mic_selector.pick_input_device(force=True)
    assert pick["index"] == 5


def test_level_still_decides_when_someone_speaks(monkeypatch):
    """真有人说话时（电平超过 ALIVE_RMS），电平才该重新参与比较。"""
    devices = [
        _dev(1, "麦克风阵列 (Intel)", is_default=True, host_api="MME"),
        _dev(5, "麦克风阵列 (Intel)", host_api="Windows DirectSound"),
        _dev(9, "麦克风阵列 (Intel)", host_api="Windows WASAPI"),
    ]
    _stub(monkeypatch, devices, {1: 0.0004, 5: 0.0004, 9: 0.08})

    pick = mic_selector.pick_input_device(force=True)
    # WASAPI API 更优先，且这次它确实听到了声音
    assert pick["index"] == 9
    assert pick["silent"] is False


def test_no_device_probed_at_all(monkeypatch):
    """一条都打不开（多半是系统隐私设置挡了）时 index 为 None。"""
    _stub(monkeypatch, [_dev(1, "麦克风阵列 (Intel)", is_default=True)], {})

    pick = mic_selector.pick_input_device(force=True)

    assert pick["index"] is None
    assert pick["silent"] is True


def test_marked_dead_device_is_skipped(monkeypatch):
    """被证实无声音的通道要进黑名单，换设备重试时别再选回它。"""
    devices = [_dev(5, "麦克风阵列 (Intel)"), _dev(7, "麦克风阵列 (Intel)")]
    _stub(monkeypatch, devices, {5: 0.02, 7: 0.018})

    first = mic_selector.pick_input_device(force=True)
    assert first["index"] == 5

    mic_selector.mark_dead(5)
    second = mic_selector.pick_input_device(force=True)
    assert second["index"] == 7


def test_explicit_device_is_preferred(monkeypatch):
    """用户在设置里指定了设备时，尊重他的选择。"""
    devices = [_dev(1, "麦克风阵列 (Intel)", is_default=True), _dev(5, "麦克风阵列 (Intel)")]
    _stub(monkeypatch, devices, {1: 0.03, 5: 0.02})

    pick = mic_selector.pick_input_device(preferred=5, force=True)

    assert pick["index"] == 5


def test_result_is_cached(monkeypatch):
    """探测要开流、有耗时，同一会话内不该反复探测。"""
    devices = [_dev(1, "麦克风阵列 (Intel)", is_default=True)]
    calls = []

    def fake_probe(index, ms=mic_selector.PROBE_MS):
        calls.append(index)
        return {"avg": 0.02, "peak": 0.04}

    monkeypatch.setattr(mic_selector, "list_input_devices", lambda: devices)
    monkeypatch.setattr(mic_selector, "probe_device", fake_probe)

    mic_selector.pick_input_device(force=True)
    mic_selector.pick_input_device()
    mic_selector.pick_input_device()

    assert len(calls) == 1
