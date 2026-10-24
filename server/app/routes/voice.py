"""Voice/TTS/STT API routes."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import time
import wave

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.tts import (
    DEFAULT_VOICE,
    list_voices,
    synthesize,
    synthesize_to_bytes,
    synthesize_to_file,
)
from .. import output_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# ── Server-side recording state ────────────────────────────────────────
_recording_state: dict = {
    "active": False,
    "frames": [],
    "start_time": 0,
    # --- VAD 段（说完了自动停，不再固定傻等一个窗口）---
    "vad": False,        # 本次录音是否启用了 VAD 自动停
    "idle": False,       # VAD 到最后都没等到人声（区别于"说了但没听清"）
    "speech_seen": False,
    "levels": [],        # 每帧 RMS，供 stop 做端点裁剪
    "silence_thr": 0.006,  # 本次录音实际生效的静音门槛（动态底噪推导）
    "speech_thr": 0.010,   # 同上，说话门槛
    # 本次录音的 VAD 状态机（mic_capture 的回调结束后由 stop 端点取走）
    "collector": None,
    # 用于从共享麦克风摘除本次监听者的幂等函数，同理由 stop 端点取走
    "detach": None,
}

# VAD 判定"有人在说话"的门槛**下限**。
#
# 不能只用一个固定值：本机安静环境的底噪 RMS 实测 0.00496，和 0.006 几乎
# 贴在一起，环境稍有动静就越过阈值 —— 表现为"VAD 认为一直在说话"，录满
# 上限才停，然后把大段静音送进识别模型，Paraformer 会在静音上产生幻觉
# （实测输出过「高志正选人上台后首位因丑闻辞职的歌了此时距离」这种胡话）。
#
# 所以真正生效的阈值是运行时按底噪推导的：见 _vad_thresholds。
_VAD_SPEECH_RMS_FLOOR = 0.010
_VAD_SILENCE_RMS_FLOOR = 0.006
# 底噪的倍数：说话要比环境明显更响才算（实测人声 0.02~0.1，底噪 0.005）
_VAD_SPEECH_FACTOR = 3.0
_VAD_SILENCE_FACTOR = 2.0


class RecordStartRequest(BaseModel):
    """VAD 模式的可选参数。缺省 vad=false 时行为与旧版完全一致。"""

    vad: bool = False
    # 说完话后连续静音多久算"说完了"
    silence_ms: int = 700
    # 从开始说话算起，单次采集的硬上限
    max_ms: int = 8000
    # 开始说话后至少录多长才允许判停（防止一个喷麦音就截断）
    min_ms: int = 600
    # 一直在等、始终没人说话的放弃时长
    leading_ms: int = 5000


def _vad_thresholds(levels: list[float]) -> tuple[float, float]:
    """按实测底噪推导 (说话门槛, 静音门槛)。

    底噪取已采到的前几帧的中位数（leading 阶段就是纯环境声），再乘一个
    倍数，并设下限——麦克风增益差异很大，固定阈值必然在某台机器上失灵。
    """
    sample = [v for v in levels[:8] if v > 0] or [0.002]
    noise = sorted(sample)[len(sample) // 2]
    speech = max(noise * _VAD_SPEECH_FACTOR, _VAD_SPEECH_RMS_FLOOR)
    silence = max(noise * _VAD_SILENCE_FACTOR, _VAD_SILENCE_RMS_FLOOR)
    # 静音门槛必须低于说话门槛，否则状态机一进说话态就出不来（迟滞）
    if silence >= speech:
        silence = speech * 0.6
    return speech, silence


def _trim_to_speech(frames: list[bytes], levels: list[float], silence_thr: float):
    """裁掉首尾静音，只把真正有人声的那段送去识别。

    识别模型（Paraformer / Whisper / Vosk 都一样）在大段静音上会输出
    幻觉文本，接口照样返回 200，只有跑一次才知道错了。所以 VAD 模式下
    必须裁剪：静音不是"没听清"，是"没说话"。
    """
    if not frames:
        return [], []
    frame_ms = 4096 / 16000 * 1000
    pad_before = max(1, int(200 / frame_ms))
    pad_after = max(1, int(300 / frame_ms))

    loud = [i for i, lv in enumerate(levels) if lv >= silence_thr]
    if not loud:
        return [], []
    start = max(0, min(loud) - pad_before)
    end = min(len(frames), max(loud) + 1 + pad_after)
    return frames[start:end], levels[start:end]


class RecordStopRequest(BaseModel):
    """手动立即停：VAD 模式下默认"等说完"，用户点停止按钮时传
    ``{"immediate": true}`` 强制截断。"""

    immediate: bool = False


class VadCollector:
    """一次 VAD 采集的状态机，每帧由共享麦克风在回调线程里喂进来。

    为什么从「写死在闭包里」改成独立类
    ------------------------------------
    旧实现的VAD逻辑是 ``start_recording`` 里的一个闭包，靠 ``nonlocal`` 读写
    几个计数器。它没法脱离真实的 PortAudio 流单独测试——而"连续对话第二轮
    开始整段录不到声音"这个 bug 恰恰最需要单测守住。抽成类之后可以直接喂
    合成帧，断言idle/判停分支。

    另一个原因是状态机现在要跨请求存活：共享麦克风只在 `/record/stop`
    时才摘监听者，中间这段状态必须有地方放，闭包撑不住。
    """

    def __init__(self, params: "RecordStartRequest") -> None:
        self._params = params
        self.frame_ms = 4096 / 16000 * 1000  # 256ms
        self.silence_run_ms = 0.0            # 说话后连续静音的累计时长
        self.speech_ms = 0.0                 # 已录到的"说话"时长
        # 阈值要等采到几帧环境声才定得下来；定下来之前先不判停
        self.calibrated = False
        self.speech_thr = _VAD_SPEECH_RMS_FLOOR
        self.silence_thr = _VAD_SILENCE_RMS_FLOOR
        self.frames: list[bytes] = []
        self.levels: list[float] = []
        self.speech_seen = False
        self.idle = False
        self.started_at = time.time()
        # 诊断用：这一轮听到的最大电平。
        # 没有它，"第二轮录不到声音"就只能靠猜——用户说他说了话，日志说
        # 全是静音，两边各执一词。有了峰值就能判断是麦克风哑了、还是人
        # 根本没在那一刻开口。
        self.peak_rms = 0.0
        # 判停原因：leading / silence / max_ms / manual
        self.stop_reason: str | None = None

    def _calibrate(self) -> None:
        """用开头几帧定「说话 / 静音」门槛。

        **不能无条件把这几帧当底噪**。用户常常在录音一开始就开口——尤其
        是连续对话里"上一句播完立刻接话"这种最常见的情形——那时前几帧
        本身就是人声。若照中位数估成环境音，门槛会被抬到人声的 3 倍，
        于是用户整句话全都被判成"没达到说话门槛"，表现是：明明说了话，
        却录满整轮然后 idle，界面上就是"聆听着就没下文了"。

        所以开头就有明显音量时直接退回默认下限：那不是底噪，是有人在说话。
        """
        early = self.levels[:3]
        if max(early) >= _VAD_SPEECH_RMS_FLOOR * 3:
            self.speech_thr = _VAD_SPEECH_RMS_FLOOR
            self.silence_thr = _VAD_SILENCE_RMS_FLOOR
        else:
            self.speech_thr, self.silence_thr = _vad_thresholds(self.levels)
        self.calibrated = True
        logger.info(
            "VAD 阈值已标定：说话>=%.4f 静音<%.4f%s",
            self.speech_thr,
            self.silence_thr,
            "（开口即有人声，改用默认下限）"
            if max(early) >= _VAD_SPEECH_RMS_FLOOR * 3
            else "",
        )

    def consume(self, data: bytes) -> None:
        """收一帧。必须在音频回调线程里被调用，因此全程异常不出界。"""
        self.frames.append(data)
        rms = _rms_of_raw_pcm(data)
        self.levels.append(rms)
        if rms > self.peak_rms:
            self.peak_rms = rms

        # 前几帧是纯环境声，拿它当底噪定阈值（麦克风增益各机不同）
        if not self.calibrated and len(self.levels) >= 3:
            self._calibrate()
        if not self.calibrated:
            return

        dur_ms = (time.time() - self.started_at) * 1000
        if rms >= self.speech_thr:
            self.speech_seen = True
            self.speech_ms += self.frame_ms
            self.silence_run_ms = 0.0
        elif not self.speech_seen:
            # 还没人开口：超过 leading_ms 就放弃这一轮
            if dur_ms >= self._params.leading_ms:
                self.idle = True
                self.stop_reason = "leading"
        else:
            self.silence_run_ms += self.frame_ms
            # 说过了话、且满足最短时长、静音够长 = 说完了。
            # 这里不置任何"停"的标志：结束与否由 finished 现算，避免状态
            # 在两个地方各存一份而对不上。
            if self.silence_run_ms >= self._params.silence_ms and self.speech_ms >= self._params.min_ms:
                self.stop_reason = "silence"
            elif dur_ms >= self._params.max_ms:
                self.stop_reason = "max_ms"

    @property
    def finished(self) -> bool:
        """这一轮是否已经可以收尾（判停 / 等满 leading 也没人开口）。"""
        if self.idle:
            return True
        if not self.calibrated or not self.speech_seen:
            return False
        return (self.silence_run_ms >= self._params.silence_ms
                and self.speech_ms >= self._params.min_ms) or \
               ((time.time() - self.started_at) * 1000 >= self._params.max_ms)

    def trimmed(self):
        """裁掉首尾静音后的音频片段，交给识别模型。"""
        return _trim_to_speech(self.frames, self.levels, self.silence_thr)


def _get_pyaudio():
    """Lazily import and return pyaudio (avoids startup cost).

    PyAudio is a plain venv dependency now (see requirements.txt). The old
    vendored copy at ``jarvis/pyaudio_pkg`` was removed in the launcher
    clean-up — if this import fails, install it into the project venv:
    ``server\\.venv\\Scripts\\python.exe -m pip install pyaudio==0.2.14``
    """
    try:
        import pyaudio
    except ImportError as exc:  # pragma: no cover - environment problem
        raise RuntimeError(
            "pyaudio 未安装，麦克风录音不可用。请在项目 venv 执行："
            "server\\.venv\\Scripts\\python.exe -m pip install pyaudio==0.2.14"
        ) from exc
    return pyaudio


# 安静环境的本底 RMS 通常在 0.001 以下，正常说话在 0.02~0.1 之间。
# 取 0.004 作为"确实收到了声音"的门槛：低于它基本可以断定麦克风被系统
# 禁用、选错了设备，或者用户根本没出声。
_SILENCE_RMS = 0.004


def _rms_of_raw_pcm(raw: bytes) -> float:
    """算出 16-bit PCM 的平均音量（RMS）。"""
    if not raw:
        return 0.0
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _level_payload(rms: float) -> dict:
    """把 RMS 换算成前端能直接展示的百分比。0.1 记为 100%。"""
    return {
        "level": round(rms, 5),
        "level_pct": min(100, round(rms / 0.1 * 100)),
    }


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    as_file: bool = False


# ------------------------------------------------------------------
# Voice listing
# ------------------------------------------------------------------

@router.get("/voices")
async def get_voices():
    """List available TTS voices."""
    return await list_voices()


# ------------------------------------------------------------------
# Text-to-Speech
# ------------------------------------------------------------------

@router.post("/tts")
async def text_to_speech(req: TTSRequest):
    """Synthesize text to speech.

    优先 MP3（在线），在线不可用时自动退回本地合成并返回 WAV —— 所以
    media_type 必须跟着结果走，写死 audio/mpeg 会让本地音频播不出来。
    """
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    try:
        audio_bytes, media_type = await synthesize(req.text, req.voice)
    except Exception as e:
        raise HTTPException(500, f"TTS synthesis failed: {str(e)}")

    # 出声前先锁住"耳朵"：浏览器拿到字节到真正播出还有一段时间，
    # 这段空档里唤醒引擎/录音必须已经知道"马上要出声了"。
    output_lock.mark_output(_estimate_audio_seconds(audio_bytes, media_type, req.text))

    suffix = "wav" if media_type.endswith("wav") else "mp3"
    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename=speech.{suffix}"},
    )


# 单次上锁的上限。估得再离谱也不能让用户干等半分钟才轮到说话。
_MAX_LOCK_SECONDS = 20.0


def _estimate_audio_seconds(audio_bytes: bytes, media_type: str, text: str) -> float:
    """估算这段音频会播多久，用来给出声锁定时长。

    WAV 能精确算（字节数 / 采样率 / 位深）；MP3 只能估（中文正常语速约
    5 字/秒，再给 1.5 秒底）。估短了锁就提前放开会串音，估长了只是多等
    一会儿——所以宁长勿短。
    """
    try:
        if media_type.endswith("wav") and len(audio_bytes) > 44:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 16000
                if rate > 0:
                    return min(frames / float(rate), _MAX_LOCK_SECONDS)
    except Exception:
        pass

    # MP3 没法从头部可靠拿时长（不同 TTS 的码率差很多：实测同一段文本
    # 按 128kbps 算会得出 18 秒，实际只有 7 秒）。所以主用**语速估**：
    # 中文正常语速约 4.5 字/秒；再取字节数按 320kbps 反推的下限兜底，
    # 防止语速估得太短、锁提前放开又串音。
    estimated = len(text) / 4.5
    by_bytes = len(audio_bytes) * 8 / 320000.0
    return min(max(estimated, by_bytes, 1.0), _MAX_LOCK_SECONDS)


@router.post("/tts/file")
async def text_to_speech_file(req: TTSRequest):
    """Synthesize text to speech. Returns file path for streaming playback.

    同样走统一入口，因此在线不可用时返回的是 WAV 文件而不是 MP3。
    """
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    try:
        audio_bytes, media_type = await synthesize(req.text, req.voice)
    except Exception as e:
        raise HTTPException(500, f"TTS synthesis failed: {str(e)}")

    suffix = "wav" if media_type.endswith("wav") else "mp3"
    with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    return FileResponse(tmp_path, media_type=media_type)


# ------------------------------------------------------------------
# Speech-to-Text
# ------------------------------------------------------------------

@router.get("/stt-status")
async def stt_status():
    """报告本地 STT 模型的实际位置与可用性。

    打包版最常见的故障是 models/ 没打进 exe，表现却是"识别一直返回空"，
    看不出根因。这个端点把模型路径、搜索过的目录、是否 frozen 全部暴露，
    排障时一眼就能判断模型有没有被打包进去。
    """
    try:
        from ..stt import model_status
        return model_status()
    except Exception as e:
        logger.exception("STT status failed")
        raise HTTPException(500, f"STT status failed: {str(e)}")


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """Transcribe audio to text.

    Strategy:
    1. Vosk local STT (offline, fast, no API needed)
    2. HuggingFace Whisper Tiny (free, no key needed)
    3. Configured LLM endpoint's Whisper-compatible API
    """
    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "recording.webm"
        content_type = audio.content_type or "audio/webm"

        if len(audio_bytes) < 100:
            raise HTTPException(400, "Audio too short (no speech detected)")

        # --- Strategy 1: Vosk local STT (preferred - offline, fast) ---
        try:
            from ..stt import is_model_available, transcribe_wav
            if is_model_available():
                import asyncio
                text = await asyncio.to_thread(transcribe_wav, audio_bytes)
                if text:
                    logger.info("STT (Vosk local): '%s'", text[:80])
                    return {"text": text}
                else:
                    logger.info("STT (Vosk local): no speech detected")
                    return {"text": "", "info": "未检测到语音"}
        except Exception as e:
            logger.warning("Vosk STT failed: %s", e)

        # 本地模型可用时上面已经返回；能走到这里说明本地不可用。
        # 离线部署（打包版）下继续联网只会让用户每次说话都干等十几秒，
        # 因此允许用 JARVIS_STT_OFFLINE_ONLY 直接关掉网络回退。
        if os.environ.get("JARVIS_STT_OFFLINE_ONLY") == "1":
            logger.warning("STT: 本地模型不可用且已开启离线模式，跳过网络回退")
            return _stt_unavailable_response()

        # --- Strategy 2: HuggingFace free Whisper ---
        # 超时刻意压到 8s：语音链路的体验预算是 3s，这里已经是"本地模型缺失"
        # 的降级路径，再让用户等满 60s 只会让界面看起来卡死。
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api-inference.huggingface.co/models/openai/whisper-tiny",
                    headers={"Content-Type": content_type},
                    content=audio_bytes,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("text", "").strip()
                    if text:
                        logger.info("STT (HuggingFace): '%s'", text[:50])
                        return {"text": text}
        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning("HF Whisper unavailable")

        # --- Strategy 3: Configured LLM Whisper API ---
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.llm_base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    files={
                        "file": (filename, audio_bytes, content_type),
                    },
                    data={"model": "whisper-1"},
                )
                if resp.status_code == 200:
                    result = resp.json()
                    text = result.get("text", "").strip()
                    if text:
                        logger.info("STT (LLM Whisper): '%s'", text[:50])
                        return {"text": text}
        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning("LLM Whisper unavailable")

        # --- Fallback ---
        logger.warning("All STT services unavailable")
        return _stt_unavailable_response()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(500, f"STT failed: {str(e)}")


def _stt_unavailable_response() -> dict:
    """Return a graceful response when no STT service is available."""
    return {
        "text": "",
        "error": "语音识别服务暂时不可用，请稍后重试或使用文字输入",
    }


# ------------------------------------------------------------------
# Server-side recording + transcription
# Records directly from system mic using pyaudio, then transcribes.
# This bypasses browser getUserMedia AGC issues.
# ------------------------------------------------------------------

@router.post("/record/start")
async def start_recording(req: RecordStartRequest | None = None):
    """Start recording from system microphone via pyaudio.

    默认行为与旧版一致（手动 start / stop）。传 ``{"vad": true}`` 时启用
    「说完自动停」：检测到人声后连续静音 ``silence_ms`` 即认为说完了，
    录音线程自动收尾，``/record/stop`` 变成"等结果"的语义。
    """
    if _recording_state["active"]:
        return {"status": "already_recording"}

    # 兼容不传 body 的旧调用方（按钮路径）
    params = req or RecordStartRequest()

    # 播报期间一律不开录：麦离扬声器太近，这时候录进去的是贾维斯自己的
    # 声音，识别出来一堆胡话，连续对话就是这么被拖垮的。服务端等锁放开
    # 再开，比让前端自己判时序可靠（前端的 isPlaying 要等字节到才置位，
    # 中间有 1~2 秒空档，实测就是那几秒把播报声录了进去）。
    waited = 0.0
    while output_lock.is_output_active() and waited < 8.0:
        await asyncio.sleep(0.1)
        waited += 0.1
    if waited > 0:
        logger.info("录音延后 %.1fs：等待播报结束", waited)

    try:
        from .. import mic_capture
        from ..mic_selector import pick_input_device

        # 和上一条 /record/stop 可能还没摘干净的监听者做个了断，避免同一帧
        # 被两个状态机各收一遍（表现为识别文本莫名其妙重复一句）。
        _detach_listener()

        collector = VadCollector(params)
        picked = pick_input_device().get("index")

        mic_capture.ensure(picked)
        _recording_state["frames"] = []
        _recording_state["active"] = True
        _recording_state["start_time"] = time.time()
        _recording_state["vad"] = params.vad
        _recording_state["idle"] = False
        _recording_state["levels"] = []
        _recording_state["silence_thr"] = _VAD_SILENCE_RMS_FLOOR
        _recording_state["speech_thr"] = _VAD_SPEECH_RMS_FLOOR
        _recording_state["collector"] = collector

        def _frames_sink(data: bytes) -> None:
            # 这里跑在 PortAudio 的回调线程里。mic_capture 已经兜了异常，
            # 但状态必须同步回 _recording_state —— stop 端点读的是它。
            collector.consume(data)
            if collector.finished:
                _recording_state["idle"] = collector.idle
                _recording_state["active"] = False

        _recording_state["detach"] = mic_capture.add_listener(_frames_sink, picked)
        logger.info(
            "录音开始（共享流 vad=%s device=%s listeners=%s）",
            params.vad,
            picked,
            mic_capture.stats()["listeners"],
        )
        return {"status": "recording", "vad": params.vad}

    except HTTPException:
        raise
    except Exception as e:
        _recording_state["active"] = False
        logger.error("Failed to start recording: %s", e)
        raise HTTPException(500, f"Failed to start recording: {str(e)}")


def _detach_listener() -> None:
    """摘掉上一次录音残留的监听者（幂等）。"""
    detach = _recording_state.pop("detach", None)
    if callable(detach):
        try:
            detach()
        except Exception:
            logger.debug("摘除录音监听者失败，忽略", exc_info=True)


@router.post("/record/stop")
async def stop_recording_and_transcribe(req: RecordStopRequest | None = None):
    """Stop recording, transcribe, and return text.

    VAD 模式下这句话的默认语义是"等录音自然结束"：自动停发生在线程里，
    这里最多等到 max 上限，随后照常转写。手动路径（按钮）行为不变：
    调用即停、立即转写；VAD 采集中途要手动截断时传 ``immediate=true``。
    """
    params = req or RecordStopRequest()
    # 先记住本次是不是 VAD 录音：下面马上要把标志清掉，而 stop 后面的
    # 端点裁剪还要用它。先清零再读会静默跳过裁剪，静音又被送去识别。
    was_vad = bool(_recording_state.get("vad"))
    if not _recording_state["active"] and not was_vad:
        raise HTTPException(400, "Not recording")

    # VAD 模式：等状态机自己判停（最多再等 10s，防回调线程死了挂住请求）
    if was_vad and not params.immediate:
        waited = 0.0
        while _recording_state["active"] and waited < 10.0:
            await asyncio.sleep(0.1)
            waited += 0.1
    _recording_state["active"] = False
    _recording_state["vad"] = False
    # 立刻停止收帧，再去读状态机里攒下的东西。
    # 顺序不能反：共享麦克风是所有录音共用的，晚摘一秒就多录一秒垃圾，
    # 而 frames/levels 现在由 collector 自己持有，摘掉也不影响取结果。
    _detach_listener()

    collector: VadCollector | None = _recording_state.pop("collector", None)
    frames = list(collector.frames) if collector else list(_recording_state["frames"])
    levels = list(collector.levels) if collector else list(_recording_state.get("levels") or [])
    duration = time.time() - _recording_state["start_time"]
    idle = bool(_recording_state.get("idle")) or bool(collector.idle if collector else False)
    silence_thr = collector.silence_thr if collector else _recording_state.get(
        "silence_thr", _VAD_SILENCE_RMS_FLOOR
    )
    _recording_state["frames"] = []
    _recording_state["levels"] = []

    # VAD 模式：先裁掉首尾静音再送去识别。识别模型在大段静音上会输出
    # 幻觉文本（实测 Paraformer 输出过一整句无关的话），接口照样返回 200，
    # 只有跑一次才发现——所以必须在送进模型前就裁掉。
    if was_vad and frames:
        trimmed, _ = _trim_to_speech(frames, levels, silence_thr)
        if trimmed:
            logger.info(
                "VAD 端点裁剪：%d 帧 → %d 帧（去掉首尾静音）", len(frames), len(trimmed)
            )
            frames = trimmed
        else:
            # 整段都没达到说话门槛 = 没人开口
            idle = True

    logger.info(
        "录音结束：%.1fs %d帧 idle=%s 原因=%s 峰值rms=%.5f 说话门槛=%.4f 说话时长=%.0fms%s",
        duration,
        len(frames),
        idle,
        (collector.stop_reason if collector else "unknown"),
        (collector.peak_rms if collector else 0.0),
        (collector.speech_thr if collector else 0.0),
        (collector.speech_ms if collector else 0.0),
        "  ⚠ 整段没达到说话门槛——用户可能压根没开口，或麦克风这条流是哑的"
        if idle and collector and collector.peak_rms < (collector.speech_thr or 0.01)
        else "",
    )

    # VAD 等了 leading_ms 也没等到人声：这是连续对话的正常空闲退出，
    # 不是故障。返回 idle=true 让前端安静地结束这一轮，别拿"麦克风故障"
    # 的文案去吓用户。
    if idle:
        return {"text": "", "duration": round(duration, 1), "idle": True}

    if len(frames) < 2:
        return {"text": "", "duration": round(duration, 1), "error": "录音太短，请按住说完再松开"}

    raw = b"".join(frames)
    rms = _rms_of_raw_pcm(raw)

    # Encode as WAV in memory
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(raw)
    wav_bytes = buf.getvalue()

    logger.info(
        "Server recording: %.1fs, %d frames, %d bytes, rms=%.5f",
        duration, len(frames), len(wav_bytes), rms,
    )

    # 静音就直接告诉用户，不要浪费时间跑识别。
    # 麦克风被 Windows 隐私设置挡掉时 pyaudio 仍能"正常"返回全零数据，
    # 不检测音量的话用户只会看到"说了话但什么都没发生"。
    if rms < _SILENCE_RMS:
        logger.warning("Recording is silent (rms=%.5f) — mic may be muted", rms)
        return {
            "text": "",
            "duration": round(duration, 1),
            "error": "麦克风没有收到声音，请检查系统麦克风权限或默认输入设备",
            **_level_payload(rms),
        }

    # Transcribe using STT module
    try:
        from ..stt import transcribe_wav
        text = await asyncio.to_thread(transcribe_wav, wav_bytes)
        logger.info("Server recording STT result: '%s'", text[:80])
        if text.strip():
            return {"text": text, "duration": round(duration, 1), **_level_payload(rms)}
        # 有声音但没识别出内容：和"没收到声音"是两种不同的故障，必须分开说，
        # 否则用户无法判断该去查权限还是该重说一遍。
        return {
            "text": "",
            "duration": round(duration, 1),
            "error": "听到了声音但没听清，请靠近麦克风、稍微说慢一点",
            **_level_payload(rms),
        }
    except Exception as e:
        logger.error("Server recording STT error: %s", e)
        raise HTTPException(500, f"STT failed: {str(e)}")


def _record_blocking(seconds: float) -> tuple[bytes, dict | None]:
    """同步录一小段，返回 (原始 PCM, 实际使用的输入设备信息)。

    走 mic_capture 的共享流，而不是自己 ``PyAudio()`` 开一条。
    理由与 /record/start 完全一致：这条路径（/mic-check 与诊断用）同样会
    在唤醒监听正在跑的时候被调用，自己开一条就等于在同一个设备上叠第三
    条流，一不小心就把别人挤成哑通道。

    必须在线程里跑：它要挂着等帧到齐，不能堵住事件循环。
    """
    from .. import mic_capture
    from ..mic_selector import pick_input_device

    pick = pick_input_device()
    picked_index = pick.get("index")

    device: dict | None = None
    try:
        import pyaudio as pa

        p = pa.PyAudio()
        try:
            dev = (
                p.get_device_info_by_index(picked_index)
                if picked_index is not None
                else p.get_default_input_device_info()
            )
            device = {
                "name": dev.get("name"),
                "sample_rate": dev.get("defaultSampleRate"),
                "index": dev.get("index"),
                "auto_picked": picked_index is not None,
                "probe_rms": pick.get("rms"),
            }
        except Exception:
            device = None
        finally:
            try:
                p.terminate()
            except Exception:
                pass
    except Exception:
        device = None

    chunks: list[bytes] = []
    target = max(1, int(16000 / mic_capture.FRAMES_PER_BUFFER * seconds))
    remove = mic_capture.add_listener(chunks.append, picked_index)
    try:
        deadline = time.time() + seconds + 1.0
        while len(chunks) < target and time.time() < deadline:
            time.sleep(0.02)
    finally:
        remove()
    return b"".join(chunks), device


@router.post("/mic-check")
async def mic_check(seconds: float = 2.0):
    """录一小段并返回实测音量，用于确认系统是否真的允许访问麦克风。

    "说了话没反应"最常见的三类原因在用户侧完全无法区分：麦克风被系统
    隐私设置挡掉、默认输入设备选错、本地模型没打包。这个端点直接把
    音量、设备名、模型状态一起返回，让用户在界面上就能定位是哪一类。
    """
    if _recording_state["active"]:
        raise HTTPException(409, "正在录音中，请先停止")

    seconds = max(0.5, min(seconds, 5.0))

    try:
        _get_pyaudio()
        raw, device = await asyncio.to_thread(_record_blocking, seconds)
    except RuntimeError as e:
        # pyaudio 没装：这类问题用户自己改不了，直接把安装提示透传出去
        raise HTTPException(500, str(e))
    except Exception as e:
        logger.exception("Mic check failed")
        raise HTTPException(500, f"麦克风检测失败：{e}")

    rms = _rms_of_raw_pcm(raw)
    try:
        from ..stt import model_status
        status = model_status()
        model_ready = bool(status.get("available"))
    except Exception:
        model_ready = False

    logger.info("Mic check: %.1fs, %d bytes, rms=%.5f", seconds, len(raw), rms)
    return {
        "duration": round(seconds, 1),
        "bytes": len(raw),
        "device": device,
        "heard_sound": rms >= _SILENCE_RMS,
        "model_ready": model_ready,
        **_level_payload(rms),
    }
