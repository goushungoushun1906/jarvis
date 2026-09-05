"""Workspace tools for JARVIS — project-level code understanding and editing.

Provides a minimal project-aware agent toolkit:
- workspace_open: set the active workspace directory
- workspace_list_files: list code files in the workspace
- workspace_search: keyword search across file names and contents
- workspace_read_file: read a file relative to workspace root
- workspace_write_file: write/create a file relative to workspace root
- workspace_run_command: run a shell command inside the workspace directory
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .tools import BaseTool, ToolResult, registry

logger = logging.getLogger("jarvis")

# Workspace state (single active workspace per process)
_active_workspace: Path | None = None

# File extensions considered "code" / project files
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt",
    ".html", ".css", ".scss", ".sass", ".less",
    ".java", ".kt", ".go", ".rs", ".cpp", ".c", ".h", ".hpp",
    ".rb", ".php", ".cs", ".swift", ".m", ".mm",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".ps1", ".bat", ".cmd",
}

IGNORED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv",
    "venv", "dist", "build", "target", ".idea", ".vscode", "release",
    "server", ".electron-data", "data", "libs", "models",
}


def _set_workspace(path: str) -> ToolResult:
    global _active_workspace
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return ToolResult(f"Workspace path does not exist: {path}", success=False)
    if not p.is_dir():
        return ToolResult(f"Workspace path is not a directory: {path}", success=False)
    _active_workspace = p
    return ToolResult(f"Workspace opened: {p}")


def _get_workspace() -> Path | None:
    return _active_workspace


def _is_code_file(path: Path) -> bool:
    return path.suffix.lower() in CODE_EXTENSIONS


def _iter_code_files(root: Path, max_files: int = 500):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            if _is_code_file(file_path):
                yield file_path
                count += 1
                if count >= max_files:
                    return


class WorkspaceOpenTool(BaseTool):
    name = "workspace_open"
    description = (
        "Open a folder as the active workspace/project. "
        "Once opened, other workspace tools can list, search, read, write, and run commands in it. "
        "Use absolute paths or paths starting with ~."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the project/work directory",
            },
        },
        "required": ["path"],
    }

    async def execute(self, path: str) -> ToolResult:
        return _set_workspace(path)


class WorkspaceListFilesTool(BaseTool):
    name = "workspace_list_files"
    description = (
        "List code and project files in the active workspace. "
        "Returns file paths relative to the workspace root."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "max_files": {
                "type": "integer",
                "description": "Maximum number of files to return (default: 100)",
                "default": 100,
            },
        },
    }

    async def execute(self, max_files: int = 100) -> ToolResult:
        ws = _get_workspace()
        if not ws:
            return ToolResult("No workspace is open. Use workspace_open first.", success=False)

        files: list[str] = []
        for fp in _iter_code_files(ws, max_files=max_files):
            try:
                rel = fp.relative_to(ws).as_posix()
                files.append(rel)
            except ValueError:
                continue

        if not files:
            return ToolResult(f"No supported project files found in {ws}.")
        return ToolResult(f"Files in workspace ({len(files)}):\n" + "\n".join(files))


class WorkspaceSearchTool(BaseTool):
    name = "workspace_search"
    description = (
        "Search for keywords across file names and contents in the active workspace. "
        "Returns matching file paths with line previews."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword or phrase to search for",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matching files to return (default: 10)",
                "default": 10,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 10) -> ToolResult:
        ws = _get_workspace()
        if not ws:
            return ToolResult("No workspace is open. Use workspace_open first.", success=False)

        query_lower = query.lower()
        results: list[str] = []

        for fp in _iter_code_files(ws, max_files=500):
            try:
                rel = fp.relative_to(ws).as_posix()
            except ValueError:
                continue

            # Match filename
            if query_lower in rel.lower():
                results.append(f"FILE: {rel}")
                if len(results) >= max_results:
                    break
                continue

            # Match content (first few lines only for speed)
            try:
                if fp.stat().st_size > 2 * 1024 * 1024:
                    continue  # skip very large files
                text = fp.read_text(encoding="utf-8", errors="ignore")
                lines = text.splitlines()
                matched_lines: list[str] = []
                for i, line in enumerate(lines):
                    if query_lower in line.lower():
                        matched_lines.append(f"  L{i+1}: {line.strip()}")
                        if len(matched_lines) >= 3:
                            break
                if matched_lines:
                    results.append(f"FILE: {rel}\n" + "\n".join(matched_lines))
            except Exception as e:
                logger.debug("workspace_search error reading %s: %s", fp, e)
                continue

            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(f"No matches found for '{query}' in {ws}.")
        return ToolResult(f"Search results for '{query}' ({len(results)} files):\n\n" + "\n\n".join(results))


class WorkspaceReadFileTool(BaseTool):
    name = "workspace_read_file"
    description = (
        "Read the contents of a file relative to the active workspace root. "
        "Use this to inspect code, configs, or documentation."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to workspace root",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum lines to read (default: 200)",
                "default": 200,
            },
        },
        "required": ["path"],
    }

    async def execute(self, path: str, max_lines: int = 200) -> ToolResult:
        ws = _get_workspace()
        if not ws:
            return ToolResult("No workspace is open. Use workspace_open first.", success=False)

        target = (ws / path).resolve()
        # Security: must be inside workspace
        try:
            target.relative_to(ws)
        except ValueError:
            return ToolResult(f"Access denied: {path} is outside workspace.", success=False)

        if not target.exists():
            return ToolResult(f"File not found: {path}", success=False)
        if not target.is_file():
            return ToolResult(f"Path is not a file: {path}", success=False)
        if target.stat().st_size > 5 * 1024 * 1024:
            return ToolResult(f"File too large to read: {path}", success=False)

        try:
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            total = len(lines)
            head = lines[:max_lines]
            result = "\n".join(head)
            if total > max_lines:
                result += f"\n\n... ({total - max_lines} more lines)"
            return ToolResult(f"Content of {path}:\n```\n{result}\n```")
        except Exception as e:
            return ToolResult(f"Failed to read {path}: {e}", success=False)


class WorkspaceWriteFileTool(BaseTool):
    name = "workspace_write_file"
    description = (
        "Write or overwrite a file relative to the active workspace root. "
        "Use this to edit code, create new files, or apply patches."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to workspace root",
            },
            "content": {
                "type": "string",
                "description": "Full new content of the file",
            },
        },
        "required": ["path", "content"],
    }

    async def execute(self, path: str, content: str) -> ToolResult:
        ws = _get_workspace()
        if not ws:
            return ToolResult("No workspace is open. Use workspace_open first.", success=False)

        target = (ws / path).resolve()
        try:
            target.relative_to(ws)
        except ValueError:
            return ToolResult(f"Access denied: {path} is outside workspace.", success=False)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(f"Successfully wrote {path} ({len(content)} chars).")
        except Exception as e:
            return ToolResult(f"Failed to write {path}: {e}", success=False)


class WorkspaceRunCommandTool(BaseTool):
    name = "workspace_run_command"
    description = (
        "Run a shell command inside the active workspace directory. "
        "Use this to run tests, install dependencies, build, or check lint. "
        "Be careful with destructive commands."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run (e.g. 'npm test', 'python -m pytest')",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
                "default": 30,
            },
        },
        "required": ["command"],
    }

    async def execute(self, command: str, timeout: int = 30) -> ToolResult:
        ws = _get_workspace()
        if not ws:
            return ToolResult("No workspace is open. Use workspace_open first.", success=False)

        # Basic safety filter
        dangerous = ["rm -rf /", "rd /s /q ", "format ", "del /f /s /q ", ":(){"]
        lowered = command.lower()
        for d in dangerous:
            if d in lowered:
                return ToolResult(f"Blocked potentially dangerous command: {command}", success=False)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=max(1, timeout),
            )
            output = proc.stdout.strip()
            err = proc.stderr.strip()
            lines: list[str] = []
            if output:
                lines.append(output)
            if err:
                lines.append(f"[stderr] {err}")
            result_text = "\n".join(lines) or "(no output)"
            if proc.returncode != 0:
                return ToolResult(
                    f"Command exited with code {proc.returncode}:\n{result_text}",
                    success=False,
                )
            return ToolResult(f"Command output:\n```\n{result_text}\n```")
        except subprocess.TimeoutExpired:
            return ToolResult(f"Command timed out after {timeout}s: {command}", success=False)
        except Exception as e:
            return ToolResult(f"Failed to run command: {e}", success=False)


def register_workspace_tools() -> None:
    """Register workspace/project-level tools with the global registry."""
    registry.register(WorkspaceOpenTool())
    registry.register(WorkspaceListFilesTool())
    registry.register(WorkspaceSearchTool())
    registry.register(WorkspaceReadFileTool())
    registry.register(WorkspaceWriteFileTool())
    registry.register(WorkspaceRunCommandTool())
