"""Tools management API."""

from pydantic import BaseModel

from fastapi import APIRouter, HTTPException

from ..approval import broker as approval_broker
from ..tools import registry
from ..mcp_client import mcp_client, CONFIG_PATH
from ..system_tools import SystemMonitorTool, ScreenshotTool
from ..browser_tools import (
    BrowserOpenTool,
    BrowserExtractTool,
    BrowserScreenshotTool,
    BrowserClickTool,
    BrowserCloseTool,
)

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.post("/screenshot")
async def screenshot(
    mode: str = "fullscreen",
    ocr: bool = True,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
):
    """Capture a screenshot and optionally run OCR on it.

    Modes: fullscreen | active_window | region.
    For region mode, provide left/top/width/height query parameters.
    """
    region = None
    if all(v is not None for v in (left, top, width, height)):
        region = {"left": left, "top": top, "width": width, "height": height}

    tool = ScreenshotTool()
    result = await tool.execute(mode=mode, ocr=ocr, region=region)
    return {"success": result.success, "text": result.content, "metadata": result.metadata}


@router.get("")
async def list_tools():
    """List all available tools with their schemas."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters_schema,
        }
        for t in registry.list_tools()
    ]


class ApprovalDecision(BaseModel):
    tool_call_id: str
    approved: bool


@router.post("/approve")
async def approve_tool_call(body: ApprovalDecision):
    """Hand a user's approval decision back to a live chat stream.

    The frontend has been POSTing here since Phase 17.3; the endpoint did not
    exist, so the 404 was swallowed and every "rejected" call ran anyway. The
    chat stream registers each pending call with the process-wide broker under
    its ``call_id``, so this request can resume a stream owned by a different
    task.

    Returns ``ok: false`` when the id is unknown — the call already completed,
    timed out, or belongs to a stream that has ended.
    """
    resolved = approval_broker.resolve(body.tool_call_id, body.approved)
    if not resolved:
        return {
            "ok": False,
            "reason": "unknown_or_expired_call_id",
            "tool_call_id": body.tool_call_id,
        }
    return {
        "ok": True,
        "tool_call_id": body.tool_call_id,
        "approved": body.approved,
    }


class MCPConfigPayload(BaseModel):
    """整个 mcp_servers.json 的内容。

    用宽松的 dict 接收：server 配置的字段会随 MCP 生态演进，在路由层写死
    模型容易把合法配置挡掉。真正的字段收敛发生在
    ``mcp_client._sanitize_server_config`` —— 未知键在那里被丢弃。
    """

    servers: dict[str, dict]


@router.get("/mcp/config")
async def get_mcp_config():
    """返回 mcp_servers.json 的原始内容（含 command/args/url）。

    注意路径：打包版读的是 exe 同级的那份，不是打进包的副本 —— 否则用户在
    设置里改完，重启后又回到旧配置（和 .env 当初踩的是同一个坑）。
    """
    try:
        return {
            "path": CONFIG_PATH,
            "config": mcp_client.read_raw_config(),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/mcp/config")
async def put_mcp_config(body: MCPConfigPayload):
    """保存 MCP 配置并立即重连。"""
    try:
        servers = await mcp_client.apply_config({"servers": body.servers})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"配置文件写入失败（目录不可写？）: {e}")
    return {"status": "ok", "servers": servers}


@router.delete("/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    """删除一个 MCP 服务器配置并重载。"""
    try:
        config = mcp_client.read_raw_config()
    except ValueError as e:
        raise HTTPException(400, str(e))

    if name not in config.get("servers", {}):
        raise HTTPException(404, f"MCP 服务器 '{name}' 不存在")

    del config["servers"][name]
    try:
        servers = await mcp_client.apply_config(config)
    except (ValueError, OSError) as e:
        raise HTTPException(500, str(e))
    return {"status": "ok", "servers": servers}


@router.get("/mcp/tools")
async def list_mcp_tools():
    """列出当前已连接 MCP 服务器提供的全部工具。"""
    return mcp_client.list_tools_info()


@router.get("/mcp/servers")
async def list_mcp_servers():
    """List configured MCP servers and their connection status."""
    return mcp_client.list_servers()


@router.post("/mcp/reload")
async def reload_mcp_servers():
    """Reload MCP server configuration and reconnect."""
    await mcp_client.reload()
    return {"status": "ok", "servers": mcp_client.list_servers()}


@router.get("/system/metrics")
async def system_metrics():
    """Return current system resource metrics for the dashboard."""
    tool = SystemMonitorTool()
    result = await tool.execute(metrics=["all"])
    return {
        "success": result.success,
        "text": result.content,
        "metrics": result.metadata,
    }


@router.post("/browser/open")
async def browser_open(url: str, max_length: int = 3000):
    """Open a URL in the headless browser and return page text."""
    tool = BrowserOpenTool()
    result = await tool.execute(url=url, max_length=max_length)
    return {"success": result.success, "text": result.content, "metadata": result.metadata}


@router.post("/browser/extract")
async def browser_extract(url: str = "", selector: str = "body", max_length: int = 3000):
    """Extract text from the current page or a URL using a CSS selector."""
    tool = BrowserExtractTool()
    result = await tool.execute(url=url, selector=selector, max_length=max_length)
    return {"success": result.success, "text": result.content, "metadata": result.metadata}


@router.post("/browser/screenshot")
async def browser_screenshot(url: str = "", full_page: bool = False):
    """Take a screenshot of the current page or a URL."""
    tool = BrowserScreenshotTool()
    result = await tool.execute(url=url, full_page=full_page)
    return {"success": result.success, "text": result.content, "metadata": result.metadata}


@router.post("/browser/click")
async def browser_click(selector: str = "", text: str = "", wait_ms: int = 1000):
    """Click an element on the current page by selector or exact text."""
    tool = BrowserClickTool()
    result = await tool.execute(selector=selector, text=text, wait_ms=wait_ms)
    return {"success": result.success, "text": result.content, "metadata": result.metadata}


@router.post("/browser/close")
async def browser_close():
    """Close the shared headless browser session."""
    tool = BrowserCloseTool()
    result = await tool.execute()
    return {"success": result.success, "text": result.content}
