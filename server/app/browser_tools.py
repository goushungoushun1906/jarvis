"""Browser automation tools for JARVIS using Playwright.

Phase 11 implementation. Provides headless Chromium browsing capabilities
so the agent can open pages, extract content, take screenshots, and click
elements.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from .tools import BaseTool, ToolResult, registry

logger = logging.getLogger("jarvis")

# ------------------------------------------------------------------
# Lazy Playwright imports
# ------------------------------------------------------------------


def _get_playwright():
    try:
        from playwright.async_api import async_playwright

        return async_playwright
    except ImportError:
        return None


# ------------------------------------------------------------------
# Shared browser manager
# ------------------------------------------------------------------


class BrowserManager:
    """Keeps a single headless browser context alive across tool calls."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def _ensure_started(self):
        pw_factory = _get_playwright()
        if pw_factory is None:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")

        if self._browser is None or not self._browser.is_connected():
            self._playwright = await pw_factory().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
            )
            self._page = await self._context.new_page()
            logger.info("BrowserManager: started headless Chromium")

    async def get_page(self):
        await self._ensure_started()
        return self._page

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            self._context = None
            self._page = None
        except Exception as e:
            logger.warning("BrowserManager close error: %s", e)


browser_manager = BrowserManager()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _strip_text(text: str, max_length: int) -> str:
    text = " ".join(text.split())
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return text


# ------------------------------------------------------------------
# browser_open
# ------------------------------------------------------------------


class BrowserOpenTool(BaseTool):
    name = "browser_open"
    description = (
        "Open a URL in a headless browser and return the page title and "
        "main text content. Use this when the user asks to browse a specific "
        "web page."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to open",
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters of text to return (default: 3000)",
                "default": 3000,
            },
        },
        "required": ["url"],
    }

    async def execute(self, url: str, max_length: int = 3000) -> ToolResult:
        try:
            page = await browser_manager.get_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait a bit for dynamic content
            await page.wait_for_timeout(500)
            title = await page.title()
            body_text = await page.inner_text("body")
            return ToolResult(
                f"Title: {title}\n\n{_strip_text(body_text, max_length)}",
                metadata={"url": url, "title": title},
            )
        except Exception as e:
            logger.error("browser_open error: %s", e)
            return ToolResult(f"Failed to open {url}: {e}", success=False)


# ------------------------------------------------------------------
# browser_extract
# ------------------------------------------------------------------


class BrowserExtractTool(BaseTool):
    name = "browser_extract"
    description = (
        "Extract readable text from the current browser page or a given URL. "
        "Supports CSS selector targeting."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Optional URL to navigate to first",
            },
            "selector": {
                "type": "string",
                "description": "Optional CSS selector to extract (default: body)",
                "default": "body",
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters to return (default: 3000)",
                "default": 3000,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        url: str = "",
        selector: str = "body",
        max_length: int = 3000,
    ) -> ToolResult:
        try:
            page = await browser_manager.get_page()
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(500)
            element = await page.query_selector(selector)
            if element is None:
                return ToolResult(f"Selector '{selector}' not found on page.", success=False)
            text = await element.inner_text()
            return ToolResult(
                _strip_text(text, max_length),
                metadata={"selector": selector, "url": url or page.url},
            )
        except Exception as e:
            logger.error("browser_extract error: %s", e)
            return ToolResult(f"Failed to extract content: {e}", success=False)


# ------------------------------------------------------------------
# browser_screenshot
# ------------------------------------------------------------------


class BrowserScreenshotTool(BaseTool):
    name = "browser_screenshot"
    description = (
        "Take a screenshot of the current browser page or a given URL. "
        "Returns a base64 PNG image."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Optional URL to navigate to first",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture the full scrollable page (default: false)",
                "default": False,
            },
        },
        "required": [],
    }

    async def execute(self, url: str = "", full_page: bool = False) -> ToolResult:
        try:
            page = await browser_manager.get_page()
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(500)
            screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return ToolResult(
                f"Browser screenshot ({len(screenshot_bytes)} bytes).\ndata:image/png;base64,{b64}",
                metadata={"url": page.url, "size": len(screenshot_bytes)},
            )
        except Exception as e:
            logger.error("browser_screenshot error: %s", e)
            return ToolResult(f"Failed to take screenshot: {e}", success=False)


# ------------------------------------------------------------------
# browser_click
# ------------------------------------------------------------------


class BrowserClickTool(BaseTool):
    name = "browser_click"
    description = (
        "Click an element on the current browser page by CSS selector or text. "
        "Use this to navigate pages, expand sections, or submit forms."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the element to click",
            },
            "text": {
                "type": "string",
                "description": "Alternative: click element containing this exact text",
            },
            "wait_ms": {
                "type": "integer",
                "description": "Milliseconds to wait after click (default: 1000)",
                "default": 1000,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        selector: str = "",
        text: str = "",
        wait_ms: int = 1000,
    ) -> ToolResult:
        if not selector and not text:
            return ToolResult("Provide 'selector' or 'text' to click.", success=False)
        try:
            page = await browser_manager.get_page()
            if selector:
                await page.click(selector, timeout=10000)
            else:
                # XPath for text matching
                xpath = f'xpath=//*[normalize-space(text())="{text}"]'
                await page.click(xpath, timeout=10000)
            await page.wait_for_timeout(wait_ms)
            title = await page.title()
            return ToolResult(
                f"Clicked successfully. Current page: {title} ({page.url})",
                metadata={"url": page.url, "title": title},
            )
        except Exception as e:
            logger.error("browser_click error: %s", e)
            return ToolResult(f"Failed to click: {e}", success=False)


# ------------------------------------------------------------------
# browser_close
# ------------------------------------------------------------------


class BrowserCloseTool(BaseTool):
    name = "browser_close"
    description = "Close the shared headless browser session to free resources."
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    async def execute(self) -> ToolResult:
        await browser_manager.close()
        return ToolResult("Browser session closed.")


# ------------------------------------------------------------------
# Register
# ------------------------------------------------------------------


def register_browser_tools() -> None:
    """Register all browser automation tools."""
    registry.register(BrowserOpenTool())
    registry.register(BrowserExtractTool())
    registry.register(BrowserScreenshotTool())
    registry.register(BrowserClickTool())
    registry.register(BrowserCloseTool())
