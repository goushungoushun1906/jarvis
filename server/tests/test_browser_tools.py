"""Phase 17.8 — browser tools: framing policy detection.

``browser_open`` can fetch a page that an ``<iframe>`` in the renderer would
flatly refuse to display. Nothing surfaces that to the user today, so the
right-hand display panel goes blank with no explanation. ``_frame_policy``
turns the response headers into a verdict the UI can explain.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from app import browser_tools
from app.browser_tools import _frame_policy


def _response(headers: dict | None):
    """Stand-in for a Playwright ``Response`` (only ``.headers`` is read)."""
    if headers is None:
        return None
    return SimpleNamespace(headers=headers)


# ---------------------------------------------------------------------------
# No policy set — the common case, must stay embeddable
# ---------------------------------------------------------------------------


def test_no_framing_headers_is_embeddable():
    ok, reason = _frame_policy(_response({"content-type": "text/html"}))
    assert ok is True
    assert reason == ""


def test_none_response_is_embeddable():
    # about:blank and similar have no response object at all.
    assert _frame_policy(None) == (True, "")


# ---------------------------------------------------------------------------
# X-Frame-Options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["DENY", "deny", "Deny"])
def test_xfo_deny_blocks(value):
    ok, reason = _frame_policy(_response({"X-Frame-Options": value}))
    assert ok is False
    assert "X-Frame-Options" in reason


def test_xfo_sameorigin_blocks():
    ok, reason = _frame_policy(_response({"X-Frame-Options": "SAMEORIGIN"}))
    assert ok is False
    assert "SAMEORIGIN" in reason


def test_xfo_allowall_does_not_block():
    assert _frame_policy(_response({"X-Frame-Options": "ALLOWALL"}))[0] is True


def test_header_lookup_is_case_insensitive():
    # Playwright lowercases header names, but never assume it.
    assert _frame_policy(_response({"X-Frame-Options": "DENY"}))[0] is False


# ---------------------------------------------------------------------------
# CSP frame-ancestors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "csp",
    [
        "default-src 'self'; frame-ancestors 'none'",
        "frame-ancestors 'self'",
        "frame-ancestors https://allowed.example",
    ],
)
def test_csp_frame_ancestors_blocks(csp):
    ok, reason = _frame_policy(_response({"content-security-policy": csp}))
    assert ok is False
    assert "frame-ancestors" in reason


def test_csp_wildcard_frame_ancestors_does_not_block():
    csp = "default-src 'self'; frame-ancestors *"
    assert _frame_policy(_response({"content-security-policy": csp}))[0] is True


def test_csp_without_frame_ancestors_does_not_block():
    csp = "default-src 'self'; script-src 'self' 'unsafe-inline'"
    assert _frame_policy(_response({"content-security-policy": csp}))[0] is True


# ---------------------------------------------------------------------------
# browser_open integration — metadata must survive to the tool result
# ---------------------------------------------------------------------------


def test_browser_open_reports_url_and_embeddability(monkeypatch):
    class FakePage:
        url = "https://final.example/landing"

        async def goto(self, url, **kwargs):
            return SimpleNamespace(
                status=200,
                headers={"content-type": "text/html"},  # no framing policy
            )

        async def wait_for_timeout(self, ms):
            return None

        async def title(self):
            return "Final Landing"

        async def inner_text(self, selector):
            return "hello   world"

    class FakeManager:
        async def get_page(self):
            return FakePage()

    monkeypatch.setattr(browser_tools, "browser_manager", FakeManager())

    tool = browser_tools.BrowserOpenTool()
    result = asyncio.run(tool.execute(url="https://short.example/"))

    assert result.success is True
    # The redirect target, not the URL we passed in.
    assert result.metadata["url"] == "https://final.example/landing"
    assert result.metadata["title"] == "Final Landing"
    assert result.metadata["embeddable"] is True


def test_browser_open_flags_a_frame_blocking_site(monkeypatch):
    class FakePage:
        url = "https://blocked.example/"

        async def goto(self, url, **kwargs):
            return SimpleNamespace(status=200, headers={"x-frame-options": "DENY"})

        async def wait_for_timeout(self, ms):
            return None

        async def title(self):
            return "Blocked"

        async def inner_text(self, selector):
            return "content still readable"

    class FakeManager:
        async def get_page(self):
            return FakePage()

    monkeypatch.setattr(browser_tools, "browser_manager", FakeManager())

    result = asyncio.run(browser_tools.BrowserOpenTool().execute(url="https://blocked.example/"))

    # Fetching still succeeds — only the embedded preview is impossible.
    assert result.success is True
    assert result.metadata["embeddable"] is False
    assert "DENY" in result.metadata["embed_block_reason"]


def test_playwright_is_importable_in_this_interpreter():
    """Regression: the browser tools silently degraded to "Playwright 未安装"
    because requirements.txt pinned playwright==1.61.1, a version that does not
    exist on PyPI — so `pip install -r requirements.txt` always failed here.
    """
    assert "playwright" in sys.modules or __import__("playwright") is not None
    from playwright.async_api import async_playwright  # noqa: F401
