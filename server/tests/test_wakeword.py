"""语音唤醒 v2 的单元测试。

这里每个用例都对应一个真实踩过的坑，不是凑覆盖率：

1. AccessKey 曾在模块 import 时只读一次 —— 用户在界面上填完还得重启整个
   应用才生效。
2. AccessKey 缺失时被静默降级成能量检测 —— 任何说话都会唤醒，比没有唤醒
   更让人恼火。
3. 打包后缺了模型文件却不报错，只是"点了没反应"，排查要花很久。
4. 播报被自己的声音反复触发（自激），需要软硬件多层守卫。
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import struct
import tempfile
import time
import wave

import pytest

from app import wakeword as ww
from app.wakeword import (
    DEFAULT_ENGINE,
    DetectionEvent,
    SherpaEngine,
    WakeConfig,
    WakeWordService,
    WakeWordStatus,
    _is_likely_builtin,
    list_input_devices,
    load_wake_config,
    read_access_key,
    sensitivity_from_threshold,
    threshold_from_sensitivity,
)
from app.wakeword_vosk import (
    VoskWakeEngine,
    build_grammar,
    discover_vosk_model,
    split_chars,
)


class FakeEngine:
    """不碰麦克风、不碰 Porcupine 的替身引擎。

    构造签名与 PorcupineEngine 一致（收一个 WakeConfig），保证被测的是
    编排层的逻辑而不是引擎本身。
    """

    name = "fake"
    sample_rate = 16000
    frame_length = 512

    def __init__(self, cfg: WakeConfig) -> None:
        self.cfg = cfg
        self.closed = False
        self.frames_seen = 0

    def process(self, pcm):
        self.frames_seen += 1
        return -1, 0.0

    def close(self) -> None:
        self.closed = True


def _config(**overrides) -> WakeConfig:
    base = dict(
        access_key="fake-key",
        keyword_paths=["jarvis_zh_windows.ppn"],
        keyword_names=["你好贾维斯"],
        sensitivities=[0.6],
        model_path="models/wakeword/porcupine_params_zh.pv",
        save_captures=False,
        cooldown_s=10.0,
    )
    base.update(overrides)
    return WakeConfig(**base)


def _service(cfg: WakeConfig) -> WakeWordService:
    return WakeWordService(config_loader=lambda: cfg, engine_factory=FakeEngine)


def _make_wav(rate: int, seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00" * int(rate * seconds * 2))
    return buf.getvalue()


# ─── 配置装载 ────────────────────────────────────────────────────────


def test_access_key_is_reread_on_every_load(monkeypatch):
    """钉住旧实现最坑的一点：key 只在 import 时读一次。"""
    calls = {"value": "key-a"}
    monkeypatch.setattr(ww, "read_access_key", lambda: calls["value"])
    monkeypatch.delenv("JARVIS_WAKE_KEYWORD_PATHS", raising=False)

    assert load_wake_config().access_key == "key-a"
    calls["value"] = "key-b"
    assert load_wake_config().access_key == "key-b"


def test_read_access_key_falls_back_to_env(monkeypatch):
    """凭据管理器不可用（或未存过）时必须静默回退到环境变量。"""
    from app import secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "get_secret", lambda name: None)
    monkeypatch.setenv("PORCUPINE_ACCESS_KEY", "env-secret")
    assert read_access_key() == "env-secret"


# ─── 启动失败必须说人话 ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_start_without_access_key_reports_error():
    """缺 key 要明确报错并给引导，绝不能悄悄降级到能量检测。

    显式指定 porcupine：只有它需要密钥，sherpa 走的是本地开放词表模型。
    """
    service = _service(_config(access_key="", engine="porcupine"))
    info = await service.start()
    assert info["status"] == WakeWordStatus.ERROR.value
    assert "AccessKey" in (info["error"] or "")
    # 引擎不该被建起来：能量检测会把任何说话都当成唤醒
    assert service._engine is None


@pytest.mark.anyio
async def test_start_without_keyword_models_reports_error():
    """这一段刻意不用替身引擎：缺.ppn 的检查就在真实工厂里。"""
    cfg = _config(keyword_paths=[], keyword_names=[], engine="porcupine")
    service = WakeWordService(config_loader=lambda: cfg)
    info = await service.start()
    assert info["status"] == WakeWordStatus.ERROR.value
    assert "关键词模型" in (info["error"] or "")


@pytest.mark.anyio
async def test_start_without_wake_words_reports_error(tmp_path):
    """连唤醒词都没配时，要明确告诉用户去哪儿写。

    这条专测 Vosk：它不吃 .ppn，唤醒词来自 keywords_raw.txt 里的中文文本。
    模型目录单独给个空目录，好让检查走到"唤醒词缺失"这一档，
    而不是先被"模型缺失"拦下。
    """
    cfg = _config(
        keyword_paths=[],
        keyword_names=[],
        engine="vosk",
        vosk_model=str(tmp_path),
    )
    service = WakeWordService(config_loader=lambda: cfg)
    info = await service.start()
    assert info["status"] == WakeWordStatus.ERROR.value
    assert "唤醒词" in (info["error"] or "")


@pytest.mark.anyio
async def test_engine_failure_does_not_leave_thread_running():
    """启动失败后不能留下一条半死的采集线程。"""
    service = _service(_config(access_key="", engine="porcupine"))
    await service.start()
    assert service._thread is None


# ─── 事件推送 ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_detection_is_pushed_to_subscriber():
    service = _service(_config())
    service._loop = asyncio.get_running_loop()
    service._engine = FakeEngine(_config())
    queue: asyncio.Queue = asyncio.Queue()
    service.subscribe(queue)

    service._on_detection(0, 1.0, 0.42, [])
    await asyncio.sleep(0.05)

    event = queue.get_nowait()
    assert event["type"] == "detection"
    assert event["payload"]["keyword"] == "你好贾维斯"
    assert event["payload"]["score"] == 1.0


@pytest.mark.anyio
async def test_slow_subscriber_never_blocks_detection():
    """慢订阅者要丢最旧事件，绝不能反压到音频采集线程上。"""
    service = _service(_config())
    service._loop = asyncio.get_running_loop()
    service._engine = FakeEngine(_config())
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    service.subscribe(queue)

    for _ in range(10):
        service._on_detection(0, 1.0, 0.1, [])
    await asyncio.sleep(0.05)
    # 没有阻塞、队列不超过上限即达标
    assert queue.qsize() <= 2


@pytest.mark.anyio
async def test_cooldown_suppresses_burst():
    """一次说话常常连着触发好几帧，冷却期内只算一次。"""
    service = _service(_config(cooldown_s=30.0))
    service._loop = asyncio.get_running_loop()
    service._engine = FakeEngine(_config())

    service._on_detection(0, 1.0, 0.3, [])
    service._on_detection(0, 1.0, 0.3, [])
    service._on_detection(0, 1.0, 0.3, [])

    assert service.get_info()["detections_total"] == 1


# ─── 运行时调参 ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_sensitivities_rebuilds_engine():
    """Porcupine 不支持运行中改灵敏度，所以要换实例 —— 但旧实例必须被关掉。"""
    cfg = _config()
    service = _service(cfg)
    service._loop = asyncio.get_running_loop()
    service._engine = old = FakeEngine(cfg)

    info = await service.set_sensitivities([0.35])
    assert info["sensitivities"] == [0.35]
    assert old.closed is True
    assert service._engine is not old


@pytest.mark.anyio
async def test_set_sensitivities_clamps_range():
    service = _service(_config())
    service._engine = FakeEngine(_config())
    info = await service.set_sensitivities([2.5, -1.0])
    assert info["sensitivities"] == [1.0, 0.0]


@pytest.mark.anyio
async def test_suspend_then_resume_restores_listening():
    service = _service(_config())
    service._status = WakeWordStatus.LISTENING
    await service.suspend()
    assert service._status == WakeWordStatus.SUSPENDED
    # 线程没跑时不能直接跳回 listening —— 那会让界面假装可用
    await service.resume()
    assert service.get_info()["status"] == WakeWordStatus.SUSPENDED.value


# ─── 打包自检 ────────────────────────────────────────────────────────


def test_self_check_flags_missing_model_file(tmp_path):
    """打包漏文件的表现永远是"没反应"，所以自检必须能一眼看出缺哪个。"""
    service = _service(_config(model_path=str(tmp_path / "missing.pv")))
    check = service.get_info()["self_check"]
    assert check["model_exists"] is False
    assert check["model_path"].endswith("missing.pv")


def test_info_is_json_serializable():
    """self_check 会直接经 HTTP 返回，任何非可序列化字段都会让接口 500。"""
    import json

    service = _service(_config())
    json.dumps(service.get_info())  # 不抛异常即通过


# ─── 离线回放 ────────────────────────────────────────────────────────


def test_detect_wav_rejects_wrong_sample_rate():
    service = _service(_config())
    service._engine = FakeEngine(_config())
    with pytest.raises(ValueError, match="采样率"):
        service._detect_wav(_make_wav(8000, 0.5))


def test_detect_wav_returns_no_hit_on_silence():
    service = _service(_config())
    service._engine = FakeEngine(_config())
    result = service._detect_wav(_make_wav(16000, 0.5))
    assert result["hit"] is False


# ─── 设备枚举 ────────────────────────────────────────────────────────


def test_list_input_devices_never_raises():
    """PyAudio 不可用时应返回空列表，不能让设置页整个白屏。"""
    assert isinstance(list_input_devices(), list)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("麦克风 (Realtek Audio)", True),
        ("内置麦克风阵列", True),
        ("Headset Microphone (USB Audio)", False),
    ],
)
def test_likely_builtin_heuristic(name, expected):
    assert _is_likely_builtin(name) is expected


# ─── 路由注册 ────────────────────────────────────────────────────────


def test_router_registers_expected_endpoints():
    from app.routes import wakeword as routes

    paths = {r.path for r in routes.router.routes}
    expected = (
        "/api/wakeword/info",
        "/api/wakeword/start",
        "/api/wakeword/stop",
        "/api/wakeword/reload",
        "/api/wakeword/devices",
        "/api/wakeword/sensitivity",
        "/api/wakeword/suspend",
        "/api/wakeword/resume",
        "/api/wakeword/test",
        "/api/wakeword/stream",
        "/api/wakeword/captures",
    )
    for path in expected:
        assert any(p.startswith(path) for p in paths), f"缺少端点 {path}"


# ─── sherpa-onnx 开放词表引擎 ────────────────────────────────────────
#
# 这些用例全部通过 SherpaEngine 的 spotter_factory 注入替身完成：不加载 onnx、
# 不碰麦克风、不需要麦克风权限，因此可以在 CI 里跑。


class FakeStream:
    """模仿真实 OnlineStream 的节奏。

    关键：is_ready 必须**会变为 False**。真实实现喂一段解码完就不再就绪；
    如果替身永远返回 True，被测代码里的 `while is_ready: decode()` 会直接
    变成死循环（这正是写替身时最容易踩的坑）。
    """

    def __init__(self, owner) -> None:
        self.owner = owner
        self.pending = 0

    def accept_waveform(self, rate, samples) -> None:
        self.owner.accepted.append((rate, samples))
        self.pending += 1

    def input_finished(self) -> None:
        self.owner.finished = True
        self.pending += 1


class FakeSpotter:
    """替身关键词识别器：每次 get_result 弹出队列里的一个关键词。"""

    def __init__(self, results) -> None:
        self._q = list(results)
        self.resets = 0
        self.accepted: list = []
        self.finished = False

    def create_stream(self, keywords=None):
        return FakeStream(self)

    def is_ready(self, stream) -> bool:
        return stream.pending > 0

    def decode_stream(self, stream) -> None:
        if stream.pending > 0:
            stream.pending -= 1

    def get_result(self, stream) -> str:
        return self._q.pop(0) if self._q else ""

    def reset_stream(self, stream) -> None:
        """真 API 就叫这个名字（不是 reset）。

        替身必须跟真 API 同名，否则「命中后清理」这条路径在单测里永远走不到，
        真机上才会炸 —— 这次就是这么发现的。
        """
        self.resets += 1


def _sherpa_config(**overrides) -> WakeConfig:
    base = dict(
        engine="sherpa",
        keyword_names=["你好贾维斯"],
        sensitivities=[0.7],
        save_captures=False,
        cooldown_s=10.0,
        sherpa_strict_delta=0.10,
    )
    base.update(overrides)
    return WakeConfig(**base)


def _spotters_for(results_main, results_strict=None, record=None):
    """返回一个按阈值区分主/严实例的替身工厂。"""
    built = {}

    def factory(threshold: float):
        key = "strict" if built and threshold > min(built) else "main"
        built[threshold] = key
        results = results_strict if key == "strict" else results_main
        spotter = FakeSpotter(results)
        if record is not None:
            record[key] = spotter
        return spotter

    return factory, built


def test_engine_defaults_to_vosk(monkeypatch):
    """默认引擎是 Vosk。

    Porcupine 要联网注册、走不通；sherpa 的 3.3M KWS 模型在部分阵列麦上
    实测召回只有 8%，而 Vosk 的 LVCSR + 受限语法是 73% 召回 / 0% 误报
    （详见 app/wakeword_vosk.py 的模块说明）。
    """
    monkeypatch.delenv("JARVIS_WAKE_ENGINE", raising=False)
    assert DEFAULT_ENGINE == "vosk"
    assert load_wake_config().engine == "vosk"

    monkeypatch.setenv("JARVIS_WAKE_ENGINE", "porcupine")
    assert load_wake_config().engine == "porcupine"

    monkeypatch.setenv("JARVIS_WAKE_ENGINE", "sherpa")
    assert load_wake_config().engine == "sherpa"


def test_sherpa_keyword_string_maps_to_index():
    """sherpa 返回的是关键词字符串，要能映射回下标。"""
    factory, _ = _spotters_for(["你好贾维斯"])
    engine = SherpaEngine(_sherpa_config(), spotter_factory=factory)

    index, score = engine.process([0] * 512)
    assert index == 0
    assert score > 0

    # 词表里没有的词不能越界，退化到 0
    factory2, _ = _spotters_for(["别的词"])
    engine2 = SherpaEngine(_sherpa_config(), spotter_factory=factory2)
    index2, _ = engine2.process([0] * 512)
    assert index2 == 0


# ─── Vosk 引擎 ───────────────────────────────────────────────────────


def test_vosk_grammar_splits_into_single_chars():
    """整词必须拆成单字序列。

    Vosk 的中文词表是按字组织的，直接丢「你好贾维斯」进去只会打一行
    `Ignoring word missing in vocabulary` 然后静默失效——表现为 0 命中
    且没有任何报错，是最难排查的一类故障，所以必须有测试守住。
    """
    assert split_chars("你好贾维斯") == "你 好 贾 维 斯"
    assert split_chars("贾维斯") == "贾 维 斯"


def test_vosk_grammar_has_base_and_variants():
    entries = build_grammar(["你好贾维斯"])
    assert "你 好 贾 维 斯" in entries
    # 同音变体：只喊名字也要能唤醒
    assert "贾 维 斯" in entries
    # 单字替换变体确实生成了
    assert any("加" in e for e in entries)
    # 但语法不能膨胀到上百条，否则误报压不住
    assert len(entries) <= 40


def test_vosk_grammar_rejects_unk_and_empty():
    """[unk] 是给噪声留的出口，绝不能被当成命中。

    只借 _match 的判定逻辑，不真的加载模型（加载要几秒且依赖本地文件）。
    """
    class _Matcher:
        _match = VoskWakeEngine._match
        _build_tails = VoskWakeEngine._build_tails

    m = _Matcher()
    m._keywords = ["你好贾维斯"]
    m._tails = m._build_tails()

    assert m._match("") == -1
    assert m._match("[unk]") == -1
    assert m._match("你 好 贾 维 斯") == 0
    assert m._match("贾 维 斯") == 0


def test_vosk_discovers_small_model_first():
    """小模型优先：实测小模型 73% 召回 / 1.58s，大模型只有 50% / 2.23s。"""
    found = discover_vosk_model(os.path.join(os.path.dirname(__file__), ".."))
    if not found:
        pytest.skip("本地没有 Vosk 模型，跳过")
    assert "small" in os.path.basename(found)


def test_sherpa_resets_stream_after_hit():
    """最关键的一条：命中后必须 reset，否则会持续自激。"""
    record: dict = {}
    factory, _ = _spotters_for(["你好贾维斯"], record=record)
    engine = SherpaEngine(_sherpa_config(dual_threshold=False), spotter_factory=factory)

    first = engine.process([0] * 512)
    assert first[0] == 0
    assert record["main"].resets == 1

    # 队列已弹空：这一次应该什么都没命中
    second = engine.process([0] * 512)
    assert second == (-1, 0.0)
    assert record["main"].resets == 1  # 没命中就不该多调 reset


def test_sherpa_dual_threshold_ladder():
    """双实例同时命中才是强信号，与 Porcupine 的分数阶梯语义保持一致。"""
    record: dict = {}
    factory, built = _spotters_for(["你好贾维斯"], ["你好贾维斯"], record=record)
    engine = SherpaEngine(_sherpa_config(), spotter_factory=factory)
    index, score = engine.process([0] * 512)
    assert (index, score) == (0, 1.0)
    assert "strict" in built.values()  # 确认真的建了第二个实例

    # 只有主实例命中 → 临界分数
    record.clear()
    factory2, _ = _spotters_for(["你好贾维斯"], [""], record=record)
    engine2 = SherpaEngine(_sherpa_config(), spotter_factory=factory2)
    assert engine2.process([0] * 512)[1] == 0.75


def test_sherpa_converts_int16_to_float32():
    """accept_waveform 收 float32；喂 int16 不报错但特征全溢出 —— 静默失败。"""
    record: dict = {}
    factory, _ = _spotters_for([], record=record)
    engine = SherpaEngine(_sherpa_config(dual_threshold=False), spotter_factory=factory)

    engine.process([32767] * 512)

    rate, samples = record["main"].accepted[-1]
    assert rate == 16000
    assert samples.dtype.name == "float32"
    assert abs(float(samples.max()) - 1.0) < 0.01  # 32767 -> 约 1.0


def test_sherpa_frame_length_matches_contract():
    """帧长必须与 Porcupine / EnergyVad 一致，否则采集循环要跟着改。"""
    factory, _ = _spotters_for([])
    engine = SherpaEngine(_sherpa_config(dual_threshold=False), spotter_factory=factory)
    assert engine.sample_rate == 16000
    assert engine.frame_length == 512
    engine.close()  # close 不能抛


def test_sensitivity_maps_inversely_to_threshold():
    """灵敏度越大越易触发，而 sherpa 阈值越小越易触发 —— 别搞反。"""
    assert threshold_from_sensitivity(0.0) > threshold_from_sensitivity(1.0)
    assert threshold_from_sensitivity(0.7) == pytest.approx(0.25, abs=1e-3)

    for s in (0.0, 0.3, 0.7, 1.0):
        assert sensitivity_from_threshold(threshold_from_sensitivity(s)) == pytest.approx(
            s, abs=1e-3
        )
    # 越界要 clamp 而不是抛异常
    assert threshold_from_sensitivity(5) == threshold_from_sensitivity(1.0)
    assert threshold_from_sensitivity(-1) == threshold_from_sensitivity(0.0)


def test_sherpa_missing_model_raises_clear_error():
    """指向不存在的模型目录时，错误信息要指明该跑哪个脚本。"""
    cfg = _sherpa_config(
        sherpa_tokens="nope/tokens.txt",
        sherpa_encoder="nope/encoder.onnx",
        sherpa_decoder="nope/decoder.onnx",
        sherpa_joiner="nope/joiner.onnx",
        sherpa_keywords_src="nope/keywords.txt",
    )
    assert cfg.sherpa_ready is False

    service = WakeWordService(config_loader=lambda: cfg)
    with pytest.raises(RuntimeError) as exc:
        service._create_engine(cfg)
    assert "download_sherpa_kws" in str(exc.value)


@pytest.mark.anyio
async def test_start_without_access_key_ok_for_sherpa():
    """回归测试：sherpa 不需要密钥，缺 key 不该进 ERROR 态。

    这里刻意让工厂抛错来短路后续流程 —— 真正走下去会打开麦克风起常驻线程，
    单测里既没必要也不可靠。只要错误信息不是「缺密钥」，就说明那道硬卡已经被
    按引擎分叉掉了。
    """

    def boom(cfg):
        raise RuntimeError("REACHED_ENGINE_FACTORY")

    cfg = _sherpa_config(access_key="")
    service = WakeWordService(config_loader=lambda: cfg, engine_factory=boom)

    info = await service.start()
    assert info["status"] == WakeWordStatus.ERROR.value
    assert "AccessKey" not in (info["error"] or "")
    assert "REACHED_ENGINE_FACTORY" in (info["error"] or "")
    assert service._thread is None


@pytest.mark.anyio
async def test_unknown_engine_reports_error():
    cfg = _sherpa_config(engine="magic")
    service = WakeWordService(config_loader=lambda: cfg)
    with pytest.raises(RuntimeError) as exc:
        service._create_engine(cfg)
    assert "未知" in str(exc.value)


def test_dll_dirs_registered_before_import(monkeypatch):
    """add_dll_directory 必须发生在 import sherpa_onnx 之前，晚一步就无效。"""
    calls: list[str] = []

    class FakeOS:
        """只替换 add_dll_directory，其余行为必须保留 —— 尤其是 os.path。"""

        def __init__(self, real_os):
            self._real = real_os
            self.environ = dict(real_os.environ)
            self.path = real_os.path
            self.sep = real_os.sep
            self.pathsep = real_os.pathsep

        def add_dll_directory(self, d):
            calls.append(("add", d))
            return None

        def isdir(self, d):
            return True

    import os as _real_os
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")

    from app import sherpa_runtime

    monkeypatch.setattr(sherpa_runtime, "os", FakeOS(_real_os))
    # 注意代码走的是 os.path.isdir()，所以这里必须用真实存在的目录，
    # 伪造路径会被"目录不存在"的检查直接 continue 掉
    real_dir = tempfile.mkdtemp(prefix="jarvis-dll-test-")
    try:
        monkeypatch.setattr(sherpa_runtime, "candidate_dirs", lambda: [real_dir])
        sherpa_runtime._ADDED.clear()
        sherpa_runtime._prepare()

        assert calls, "应当注册 DLL 目录"
        assert calls[0][0] == "add"
        assert calls[0][1] == real_dir
    finally:
        shutil.rmtree(real_dir, ignore_errors=True)

    # 顺序问题不好用电的方式断言（DLL 加载不可撤销），退而 guards 源码结构：
    # _import_sherpa 里 sherpa_runtime 必须出现在 import sherpa_onnx 之前
    import inspect

    from app.wakeword import _import_sherpa

    src = inspect.getsource(_import_sherpa)
    assert src.index("sherpa_runtime") < src.index("import sherpa_onnx")


def test_detect_wav_uses_fresh_engine():
    """回放必须走一次性引擎：流式引擎的内部状态不能污染实时监听。"""
    created = []

    def factory(cfg):
        eng = FakeEngine(cfg)
        created.append(eng)
        return eng

    cfg = _config()
    service = WakeWordService(config_loader=lambda: cfg, engine_factory=factory)
    service._engine = FakeEngine(cfg)  # 假装已经在监听

    service._detect_wav(_make_wav(16000, 0.5))

    assert created, "回放应当新建一个引擎"
    assert created[0].closed is True          # 用完必须关掉
    assert service._engine.frames_seen == 0   # 实时引擎没被灌数据


def test_detect_wav_flushes_tail():
    """短音频里关键词紧贴末尾时，靠 flush() 才不会漏。"""
    class TailEngine(FakeEngine):
        flushed = False

        def flush(self):
            self.flushed = True
            return 0, 0.75

    cfg = _config()
    service = WakeWordService(
        config_loader=lambda: cfg, engine_factory=lambda c: TailEngine(c)
    )
    result = service._detect_wav(_make_wav(16000, 0.2))
    assert result["hit"] is True
    assert result["keyword"] == "你好贾维斯"


# --- 拼音编译链路（中文源 keywords_raw.txt → 拼音 token 的 keywords.txt）---
#
# 这一组是刚性需求：模型是拼音建模的，keywords.txt 里写汉字或写错拼写都不会
# 报错，只会表现为"喊破喉咙也不醒"，是最难排查的一类静默失败。

def _fake_token_map(tokens: list[str]) -> dict:
    return {t: i for i, t in enumerate(tokens)}


def test_split_syllable_uses_orthography():
    """按书写形式切音节，而不是音理上的韵母。

    pypinyin 的 FINALS 会把 wéi 给成 uei、jūn 给成 ǖn，与模型词表的写法
    （w + éi、j + ūn）对不上，照搬会整条词条 OOV。
    """
    assert ww._split_syllable("wei") == ("w", "ei")
    assert ww._split_syllable("jun") == ("j", "un")     # ün 在 j 后写作 un
    assert ww._split_syllable("xue") == ("x", "ue")
    assert ww._split_syllable("shi") == ("sh", "i")     # zh/ch/sh 必须整体匹配
    assert ww._split_syllable("ai") == ("", "ai")       # 零声母
    assert ww._split_syllable("wu") == ("w", "u")
    assert ww._split_syllable("yi") == ("y", "i")
    assert ww._split_syllable("yuan") == ("y", "uan")


def test_pinyin_tokens_for_default_wakeword():
    """「你好贾维斯」必须编译成模型词表里真实存在的 token。"""
    pytest.importorskip("pypinyin")
    token_map = ww._load_token_map(
        os.path.join(os.path.dirname(__file__), "..", "models", "wakeword",
                     "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01", "tokens.txt")
    )
    if not token_map:
        pytest.skip("本机没有模型，跳过")

    tokens = ww.pinyin_tokens("你好贾维斯", token_map)
    assert tokens == ["n", "ǐ", "h", "ǎo", "j", "iǎ", "w", "éi", "s", "ī"]
    assert all(t in token_map for t in tokens)


def test_compile_keywords_text_format():
    """产物格式必须带 :boost 与 @显示名 —— @ 段是命中时回传的字符串。"""
    pytest.importorskip("pypinyin")
    token_map = ww._load_token_map(
        os.path.join(os.path.dirname(__file__), "..", "models", "wakeword",
                     "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01", "tokens.txt")
    )
    if not token_map:
        pytest.skip("本机没有模型，跳过")

    text = ww.compile_keywords_text(["你好贾维斯"], token_map, 2.0)
    line = text.strip().splitlines()[0]
    assert line.startswith("n ǐ h ǎo j iǎ w éi s ī :2.0 @你好贾维斯")


def test_load_sherpa_keywords_accepts_both_formats(tmp_path):
    """同一个解析器要能吃中文源和编译产物两种格式。"""
    raw = tmp_path / "keywords_raw.txt"
    raw.write_text("你好贾维斯\n", encoding="utf-8")
    assert ww._load_sherpa_keywords(str(raw)) == ["你好贾维斯"]

    compiled = tmp_path / "keywords.txt"
    compiled.write_text("n ǐ h ǎo :2.0 @你好贾维斯\n", encoding="utf-8")
    assert ww._load_sherpa_keywords(str(compiled)) == ["你好贾维斯"]


def test_ensure_sherpa_keywords_recompiles_when_raw_is_newer(tmp_path):
    """改了中文源就要重编；没改就直接复用，别每次启动都去 import pypinyin。"""
    pytest.importorskip("pypinyin")
    tokens = tmp_path / "tokens.txt"
    tokens.write_text(
        "\n".join(f"{t} {i}" for i, t in enumerate(
            ["<blk>", "n", "ǐ", "h", "ǎo", "j", "iǎ", "w", "éi", "s", "ī"]
        )),
        encoding="utf-8",
    )
    raw = tmp_path / "keywords_raw.txt"
    raw.write_text("你好贾维斯\n", encoding="utf-8")
    target = tmp_path / "keywords.txt"
    target.write_text("stale\n", encoding="utf-8")
    # 源文件要写得比产物更晚，才会触发编译（写入顺序天然是 raw 先，这里拉平）
    os.utime(raw, (time.time() + 10, time.time() + 10))

    cfg = _sherpa_config(
        sherpa_tokens=str(tokens),
        sherpa_keywords_src=str(raw),
        sherpa_keywords_file=str(target),
        sherpa_boost=2.0,
    )
    out = ww._ensure_sherpa_keywords(cfg)
    assert out == str(target)
    assert "@你好贾维斯" in target.read_text(encoding="utf-8")

    # 源没变 → 不再重写（mtime 也就不动）
    before = target.read_text(encoding="utf-8")
    os.utime(target, (time.time() + 5, time.time() + 5))
    assert ww._ensure_sherpa_keywords(cfg) == str(target)
    assert target.read_text(encoding="utf-8") == before


def test_sherpa_file_matches_chunked_filenames(tmp_path):
    """官方压缩包的文件名带 chunk-16-left-64 后缀，写死必然踩空。"""
    (tmp_path / "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx").write_text("x")
    (tmp_path / "encoder-epoch-99-avg-1-chunk-16-left-64.onnx").write_text("big")

    hit = ww._sherpa_file(
        "JARVIS_SHERPA_ENCODER_TEST", str(tmp_path),
        "encoder-epoch-99-avg-1*.int8.onnx",
        "encoder-epoch-99-avg-1*.onnx",
    )
    assert hit.endswith(".int8.onnx"), "int8 优先，省体积也省 CPU"
