"""Tools management API."""

from fastapi import APIRouter

from ..tools import registry
from ..mcp_client import mcp_client
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
