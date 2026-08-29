"""JARVIS Plugin System — extensible tool framework.

Plugins add new capabilities (tools/commands) to JARVIS without modifying core code.
Each plugin lives in ``server/plugins/<name>/`` with a ``plugin.json`` manifest
and a ``main.py`` module that subclasses :class:`BasePlugin`.
"""

from __future__ import annotations

from .base import BasePlugin, PluginCommand, PluginConfigField, ToolResult as PluginToolResult
from .registry import PluginRegistry, registry

__all__ = [
    "BasePlugin",
    "PluginCommand",
    "PluginConfigField",
    "PluginRegistry",
    "PluginToolResult",
    "registry",
]
