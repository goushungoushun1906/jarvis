"""语音唤醒路由 —— 事件驱动推送 + 诊断与调参端点。

/status、/detection 这些事件由后端采集线程直接推来，不再是前端每 500 毫秒
来问一次。旧实现那个 await asyncio.sleep(0.5) 的轮询循环，光是它自己就给
"喊完要等半天"贡献了半秒延迟。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.wakeword import list_input_devices, wake_word_service

logger = logging.getLogger("jarvis.wakeword.routes")
router = APIRouter(prefix="/api/wakeword", tags=["wakeword"])

_HEARTBEAT_SECONDS = 15.0


class StartRequest(BaseModel):
    device_index: int | None = None
    sensitivities: list[float] | None = None


class SensitivityRequest(BaseModel):
    sensitivities: list[float]


class MarkRequest(BaseModel):
    label: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ping() -> str:
    """心跳用注释帧，**不能**带 data: 行。

    前端的 SSE 帧解析器会把每个 data: 当 JSON 去解析，注释帧会被跳过。
    一旦在这里塞 data: ping，"ping" 会被当成 JSON 解析失败，表现为日志里
    每隔一会儿冒一条莫名其妙的解析错误。
    """
    return ": ping\n\n"


def _safe_capture_path(capture_id: str) -> str:
    """防目录穿越：id 来自 URL，必须确认最终路径仍落在留证目录里。"""
    from app.wakeword import _resource_path

    directory = os.path.abspath(_resource_path(wake_word_service._cfg.capture_dir))
    resolved = os.path.abspath(os.path.join(directory, f"{capture_id}.wav"))
    if os.path.commonpath([resolved, directory]) != directory:
        raise HTTPException(400, "非法的录音标识")
    return resolved


@router.get("/info")
async def get_wakeword_info():
    """当前引擎状态、配置、诊断统计与打包自检。"""
    return wake_word_service.get_info()


@router.post("/start")
async def start_wakeword(payload: StartRequest | None = None):
    body = payload or StartRequest()
    try:
        info = await wake_word_service.start(
            device_index=body.device_index,
            sensitivities=body.sensitivities,
        )
    except Exception as e:
        logger.exception("启动唤醒失败")
        raise HTTPException(500, f"启动唤醒失败：{e}")
    if info.get("status") == "error":
        # 这里不抛 500：引擎不可用是预期内的配置问题，前端要能展示引导文案
        return info
    return info


@router.post("/stop")
async def stop_wakeword():
    return await wake_word_service.stop()


@router.post("/reload")
async def reload_wakeword():
    """热重读配置（含 AccessKey），无需重启后端。"""
    return await wake_word_service.reload()


@router.get("/devices")
async def list_devices():
    """可用输入设备列表，不需要先启动引擎。"""
    return {"devices": list_input_devices()}


@router.put("/sensitivity")
async def set_sensitivity(payload: SensitivityRequest):
    try:
        return await wake_word_service.set_sensitivities(payload.sensitivities)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/suspend")
async def suspend_wakeword():
    """暂停判定（播报/手动录音期间），音频流保持打开。"""
    return await wake_word_service.suspend()


@router.post("/resume")
async def resume_wakeword():
    return await wake_word_service.resume()


@router.post("/test")
async def test_wakeword(file: UploadFile = File(...)):
    """用一段音频离线跑一遍判定，**不占用麦克风**。

    打包后的 exe 无法在开发流水里对着喊话，这条端点是验证产物是否真的
    带上模型文件的唯一手段。
    """
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(400, "请上传 16bit 单声道 16kHz 的 wav 文件")
    try:
        data = await file.read()
        return await asyncio.to_thread(wake_word_service._detect_wav, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("离线回放测试失败")
        raise HTTPException(500, f"离线回放测试失败：{e}")


@router.get("/stream")
async def stream_wakeword(request: Request):
    """事件驱动 SSE。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    wake_word_service.subscribe(queue)

    async def event_generator():
        try:
            info = wake_word_service.get_info()
            yield _sse(
                "hello",
                {
                    "status": info["status"],
                    "engine": info["engine"],
                    "keywords": info["keywords"],
                    "sensitivities": info["sensitivities"],
                    "device": info["device"],
                    "access_key_set": info["access_key_set"],
                    "error": info["error"],
                },
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _ping()
                    continue
                yield _sse(event["type"], event["payload"])
        finally:
            # 不断开会在每个断线的客户端上留一条永久订阅，越积越多
            wake_word_service.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/captures")
async def list_captures():
    """留证列表。含误报打标所需的 id 与分数。"""
    return {"captures": wake_word_service.get_captures()}


@router.get("/captures/{capture_id}")
async def download_capture(capture_id: str):
    path = wake_word_service.get_capture_path(capture_id) or _safe_capture_path(capture_id)
    if not os.path.isfile(path):
        raise HTTPException(404, "录音不存在")
    return FileResponse(path, media_type="audio/wav")


@router.post("/captures/{capture_id}/mark")
async def mark_capture(capture_id: str, payload: MarkRequest):
    if not wake_word_service.mark_capture(capture_id, payload.label):
        raise HTTPException(404, "录音不存在或标签非法")
    return {"ok": True}


@router.get("/poll")
async def poll_detection():
    """Deprecated：保留仅为兼容，前端应改用 /stream。"""
    return {"info": wake_word_service.get_info(), "detections": []}
