"""Built-in tools for JARVIS — web search, code execution, system info, calculator, memory."""

from __future__ import annotations

import asyncio
import math
import logging
import platform
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from . import memory as mem
from .system_tools import register_system_tools
from .tools import BaseTool, ToolResult, registry

logger = logging.getLogger("jarvis")


# ------------------------------------------------------------------
# Web Search Tool (Bing + DuckDuckGo fallback — no auth needed)
# ------------------------------------------------------------------


def _extract_bing_results(html: str, max_results: int) -> list[dict]:
    """Extract search results from Bing HTML using regex.

    Bing result structure:
      <li class="b_algo">
        <h2><a href="URL"><strong>title</strong> text</a></h2>
        <div class="b_caption"><p class="b_lineclamp*">snippet</p></div>
      </li>
    """
    # Strip script, style, link tags
    clean = re.sub(r'<(script|style|link)\b[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<link[^>]*/?>', '', clean)

    results: list[dict] = []

    # Find each <li class="b_algo"> block
    for m in re.finditer(r'<li\s+class="b_algo"[^>]*>(.*?)</li>', clean, re.DOTALL):
        if len(results) >= max_results:
            break
        block = m.group(1)

        # Extract title and href from <h2><a href="...">...</a></h2>
        h2_match = re.search(
            r'<h2[^>]*>\s*<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>\s*</h2>',
            block, re.DOTALL,
        )
        href = ""
        title = ""
        if h2_match:
            href = h2_match.group(1)
            # Strip inner tags (like <strong>) from title text
            title = re.sub(r'<[^>]+>', '', h2_match.group(2)).strip()

        # Extract snippet from <p class="b_lineclamp*">
        p_match = re.search(
            r'<p\s+class="b_lineclamp[^"]*"[^>]*>(.*?)</p>',
            block, re.DOTALL,
        )
        snippet = ""
        if p_match:
            snippet = re.sub(r'<[^>]+>', '', p_match.group(1)).strip()

        if title or snippet:
            results.append({
                "title": title,
                "href": href,
                "snippet": snippet,
            })

    return results


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for current information, news, facts, or answers. "
        "Returns search result titles, URLs, and content summaries. "
        "Use this when you need up-to-date information."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default: 5, max: 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 5) -> ToolResult:
        max_results = min(max(1, max_results), 10)

        # Try Bing first (accessible in China via cn.bing.com)
        result = await self._bing_search(query, max_results)
        if result:
            return result

        # Fallback: DuckDuckGo Instant Answer API
        try:
            return await self._duckduckgo_search(query)
        except Exception as e:
            logger.error("DuckDuckGo fallback also failed: %s", e)
            return ToolResult(
                f"搜索暂时无法使用（网络连接问题）。请检查网络连接后重试。",
                success=False,
            )

    async def _bing_search(self, query: str, max_results: int) -> ToolResult | None:
        """Search via cn.bing.com — accessible from China."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://cn.bing.com/search",
                    params={"q": query, "count": str(max_results)},
                    headers=headers,
                )
                resp.raise_for_status()

            results = _extract_bing_results(resp.text, max_results)

            if not results:
                logger.warning("Bing search returned no parsed results for: %s", query)
                return None

            lines: list[str] = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "").strip()
                snippet = r.get("snippet", "").strip()
                href = r.get("href", "").strip()
                if title:
                    lines.append(f"{i}. {title}")
                if snippet:
                    lines.append(f"   {snippet}")
                if href:
                    lines.append(f"   {href}")

            return ToolResult("\n".join(lines))

        except Exception as e:
            logger.error("Bing search error: %s", e)
            return None

    async def _duckduckgo_search(self, query: str) -> ToolResult:
        """Fallback: DuckDuckGo Instant Answer API (limited results)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "no_redirect": 1},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[str] = []
        abstract = data.get("Abstract", "").strip()
        if abstract:
            source = data.get("AbstractSource", "")
            results.append(f"[{source}] {abstract}")
        answer = data.get("Answer", "").strip()
        if answer:
            results.append(f"Answer: {answer}")
        definition = data.get("Definition", "").strip()
        if definition:
            results.append(f"Definition: {definition}")
        related = data.get("RelatedTopics", [])
        for topic in related[:5]:
            if isinstance(topic, dict):
                text = topic.get("Text", "").strip()
                if text:
                    results.append(f"- {text}")

        if not results:
            return ToolResult(f"No results found for: {query}", success=True)
        return ToolResult("(Limited results)\n" + "\n".join(results))


# ------------------------------------------------------------------
# Code Execution Tool (safe sandbox)
# ------------------------------------------------------------------

class CodeExecutionTool(BaseTool):
    name = "code_execution"
    description = (
        "Execute Python code in a sandboxed environment. "
        "Use this for calculations, data processing, file I/O, or any task requiring code. "
        "The code runs with a timeout of 30 seconds. Network access is disabled."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "language": {
                "type": "string",
                "enum": ["python"],
                "description": "Programming language (only python supported)",
                "default": "python",
            },
        },
        "required": ["code"],
    }

    _DANGEROUS_PATTERNS = re.compile(
        r"(?:import\s+(?:os|subprocess|shutil|sys)\s*;?"
        r"|os\.(system|popen|exec|spawn)"
        r"|subprocess\."
        r"|eval\s*\("
        r"|exec\s*\("
        r"|__import__"
        r"|open\s*\(.*(?:etc|proc|sys)"
        r"|socket\."
        r"|requests\."
        r"|urllib"
        r"|shutil\.(rmtree|move|copy))",
        re.IGNORECASE,
    )

    async def execute(self, code: str, language: str = "python") -> ToolResult:
        if language != "python":
            return ToolResult(f"Unsupported language: {language}. Only 'python' is supported.", success=False)

        # Security check
        if self._DANGEROUS_PATTERNS.search(code):
            return ToolResult(
                "Code contains potentially dangerous operations (system access, network, etc.). "
                "Only safe computation and data processing code is allowed.",
                success=False,
            )

        # Write code to temp file and execute
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                f.flush()
                temp_path = f.name

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",  # don't write .pyc files
                "-S",  # don't run site.py (isolated)
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult("Code execution timed out (30s limit).", success=False)

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            if output and error:
                result_text = f"Output:\n{output}\n\nStderr:\n{error}"
            elif output:
                result_text = output
            elif error:
                # Remove traceback noise, keep last line
                error_lines = error.strip().split("\n")
                clean_error = error_lines[-1] if error_lines else error
                return ToolResult(f"Error: {clean_error}", success=False)
            else:
                result_text = "(Code executed successfully, no output)"

            return ToolResult(result_text)

        except Exception as e:
            logger.error("Code execution error: %s", e)
            return ToolResult(f"Code execution failed: {e}", success=False)
        finally:
            try:
                import os
                os.unlink(temp_path)
            except Exception:
                pass


# ------------------------------------------------------------------
# System Info Tool
# ------------------------------------------------------------------

class SystemInfoTool(BaseTool):
    name = "system_info"
    description = "Get system information like time, date, OS details."
    parameters_schema = {
        "type": "object",
        "properties": {
            "info_type": {
                "type": "string",
                "enum": ["time", "system", "all"],
                "description": "Type of info to retrieve",
            },
        },
        "required": ["info_type"],
    }

    async def execute(self, info_type: str = "all") -> ToolResult:
        parts: list[str] = []

        if info_type in ("time", "all"):
            now = datetime.now(timezone.utc)
            parts.append(f"UTC Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        if info_type in ("system", "all"):
            parts.append(f"OS: {platform.system()} {platform.release()}")
            parts.append(f"Python: {platform.python_version()}")
            parts.append(f"Architecture: {platform.machine()}")

        if not parts:
            return ToolResult("Unknown info_type. Use 'time', 'system', or 'all'.", success=False)

        return ToolResult("\n".join(parts))


# ------------------------------------------------------------------
# Calculator Tool (safe eval with whitelist)
# ------------------------------------------------------------------

class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate a mathematical expression. Only use for math calculations."
    parameters_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate",
            },
        },
        "required": ["expression"],
    }

    # Characters and function names allowed in expressions
    _SAFE_TOKENS = re.compile(
        r"^(?:"
        r"[0-9+\-*/().,%\s]"
        r"|sqrt|pow|log|log10|log2"
        r"|sin|cos|tan|asin|acos|atan"
        r"|abs|ceil|floor|round"
        r"|pi|e"
        r")+$"
    )

    async def execute(self, expression: str) -> ToolResult:
        # Security: validate characters before eval
        cleaned = expression.strip()
        if not self._SAFE_TOKENS.match(cleaned):
            return ToolResult(
                "Expression contains disallowed characters. "
                "Only numbers, +-*/.() and math functions (sin, cos, sqrt, etc.) are allowed.",
                success=False,
            )

        # Build a safe evaluation context
        safe_globals = {
            "__builtins__": {},
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
        }
        safe_locals = {
            "pi": math.pi,
            "e": math.e,
            "sqrt": math.sqrt,
            "log": math.log,
            "log10": math.log10,
            "log2": math.log2,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "asin": math.asin,
            "acos": math.acos,
            "atan": math.atan,
            "ceil": math.ceil,
            "floor": math.floor,
        }

        try:
            result = eval(cleaned, safe_globals, safe_locals)  # noqa: S307
            return ToolResult(f"{cleaned} = {result}")
        except ZeroDivisionError:
            return ToolResult("Error: Division by zero.", success=False)
        except Exception as e:
            return ToolResult(f"Error evaluating expression: {e}", success=False)


# ------------------------------------------------------------------
# Memory Tool
# ------------------------------------------------------------------

class MemoryTool(BaseTool):
    name = "memory"
    description = "Search or save information about the user."
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "save", "list"],
                "description": "Action to perform: search, save, or list memories.",
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'search' action)",
            },
            "content": {
                "type": "string",
                "description": "Content to save (for 'save' action)",
            },
            "category": {
                "type": "string",
                "description": "Category for the memory (fact, preference, event, general)",
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str, **kwargs) -> ToolResult:
        if action == "search":
            query = kwargs.get("query", "")
            if not query:
                return ToolResult("Missing 'query' parameter for search.", success=False)
            result_text = await mem.search_and_respond(query)
            return ToolResult(result_text)

        elif action == "save":
            content = kwargs.get("content", "")
            if not content:
                return ToolResult("Missing 'content' parameter for save.", success=False)
            category = kwargs.get("category", "general")
            memory = await mem.db.add_memory(content=content, category=category, importance=0.7)
            return ToolResult(f"Memory saved: {memory['content']} (id={memory['id']})")

        elif action == "list":
            memories = await mem.db.get_memories(limit=20)
            if not memories:
                return ToolResult("No memories stored yet.")
            lines = [f"- [{m['category']}] {m['content']} (imp={m['importance']})" for m in memories]
            return ToolResult("Stored memories:\n" + "\n".join(lines))

        else:
            return ToolResult(f"Unknown action: {action}. Use 'search', 'save', or 'list'.", success=False)


# ------------------------------------------------------------------
# Web Fetch Tool (extract text content from a URL)
# ------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and extract plain text."""
    def __init__(self):
        super().__init__()
        self._text_parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "meta", "link"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "meta", "link"):
            self._skip = False
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "tr"):
            self._text_parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text_parts.append(data)

    def get_text(self) -> str:
        return "".join(self._text_parts)


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = (
        "Fetch and extract text content from a web page URL. "
        "Returns the page's main text content (stripped of HTML). "
        "Use this to read the full content of a web page."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch",
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
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()

            # Extract text from HTML
            extractor = _HTMLTextExtractor()
            extractor.feed(resp.text)
            text = extractor.get_text()

            # Clean up whitespace
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines)

            # Truncate
            if len(text) > max_length:
                text = text[:max_length] + f"\n... (truncated, total {len(text)} chars)"

            if not text:
                return ToolResult(f"Could not extract text content from: {url}", success=False)

            return ToolResult(f"Content from {url}:\n\n{text}")

        except httpx.TimeoutException:
            return ToolResult(f"Fetching {url} timed out.", success=False)
        except httpx.HTTPStatusError as e:
            return ToolResult(f"HTTP error {e.response.status_code} for {url}", success=False)
        except Exception as e:
            logger.error("Web fetch error: %s", e)
            return ToolResult(f"Failed to fetch {url}: {e}", success=False)


# ------------------------------------------------------------------
# Register all built-in tools
# ------------------------------------------------------------------

def register_tools() -> None:
    """Register all built-in tools with the global registry."""
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())
    registry.register(CodeExecutionTool())
    registry.register(SystemInfoTool())
    registry.register(CalculatorTool())
    registry.register(MemoryTool())
    register_system_tools()
