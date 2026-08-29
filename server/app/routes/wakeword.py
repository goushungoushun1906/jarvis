"""Wake word API routes — start/stop/status/poll endpoints."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.wakeword import wake_word_service, WakeWordStatus

logger = logging.getLogger("jarvis.wakeword.routes")
router = APIRouter(prefix="/api/wakeword", tags=["wakeword"])


@router.get("/info")
async def get_wakeword_info():
    """Get current wake word service status and configuration."""
    return wake_word_service.get_info()


@router.post("/start")
async def start_wakeword():
    """Start the wake word listener."""
    try:
        result = await wake_word_service.start()
        return result
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        logger.error("Failed to start wake word: %s", e)
        raise HTTPException(500, f"Failed to start wake word: {str(e)}")


@router.post("/stop")
async def stop_wakeword():
    """Stop the wake word listener."""
    return await wake_word_service.stop()


@router.get("/poll")
async def poll_detection():
    """Poll for recent wake word detections (non-blocking).

    Frontend should call this every ~500ms for live updates.
    Returns current status, peak confidence, and any new detections.
    """
    detections = wake_word_service.get_recent_detections()
    info = wake_word_service.get_info()
    return {
        "info": info,
        "detections": detections,
    }


@router.get("/stream")
async def stream_wakeword():
    """SSE stream for real-time wake word status (plain text/event-stream)."""

    async def event_generator():
        last_status = ""

        while True:
            try:
                info = wake_word_service.get_info()
                current_status = info["status"]

                if current_status != last_status:
                    yield f"event: status\ndata: {json.dumps({'status': current_status, 'models': info['models']})}\n\n"
                    last_status = current_status

                yield f"event: confidence\ndata: {json.dumps({'peak_confidence': info['peak_confidence'], 'threshold': info['threshold']})}\n\n"

                detections = wake_word_service.get_recent_detections()
                for det in detections:
                    yield f"event: detection\ndata: {json.dumps(det)}\n\n"

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Wake word stream error: %s", e)
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
