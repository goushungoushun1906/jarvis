"""Plugin base class, command definitions, and lifecycle hooks."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("jarvis")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class ToolResult:
    """Result of a plugin command execution."""

    def __init__(
        self,
        content: str,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.success = success
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "success": self.success,
            "metadata": self.metadata,
        }


@dataclass
class PluginConfigField:
    """Description of a single configuration option exposed by a plugin."""

    key: str
    label: str
    type: str = "string"  # string, number, boolean, select
    default: Any = None
    description: str = ""
    options: list[str] | None = None  # for select type


@dataclass
class PluginCommand:
    """Definition of a tool / command provided by a plugin."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the parameters
    handler_name: str = ""  # method name on the plugin class; defaults to name

    def __post_init__(self):
        if not self.handler_name:
            self.handler_name = self.name

    def to_openai_tool(self) -> dict:
        """Return the command in OpenAI function-calling tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Plugin base class
# ---------------------------------------------------------------------------


class BasePlugin(ABC):
    """Base class that every JARVIS plugin must subclass.

    Subclasses should set the class-level metadata attributes and implement
    ``get_commands()`` to declare what tools they expose.

    Lifecycle (all async)::

        on_load()   → on_enable()   → execute(...)  ...
                                             → on_disable()  → on_unload()
    """

    # ------------------------------------------------------------------
    # Metadata — override in subclasses
    # ------------------------------------------------------------------

    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    min_jarvis_version: str = "1.0.0"

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        """Called when the plugin is first loaded into memory.

        Override for one-time setup such as importing dependencies or
        reading static data files.
        """
        pass

    async def on_unload(self) -> None:
        """Called when the plugin is removed from the registry.

        Override to free resources (close connections, stop timers, etc.).
        """
        pass

    async def on_enable(self) -> None:
        """Called every time the plugin transitions to *enabled*.

        Override for runtime initialisation that should repeat after a
        disable/enable cycle.
        """
        pass

    async def on_disable(self) -> None:
        """Called every time the plugin transitions to *disabled*.

        Override to pause or tear down runtime state while keeping
        the plugin loaded in memory.
        """
        pass

    # ------------------------------------------------------------------
    # Commands (tools)
    # ------------------------------------------------------------------

    def get_commands(self) -> list[PluginCommand]:
        """Return the list of tool/command definitions provided by this plugin.

        The default implementation builds commands from the ``commands``
        list in the plugin's ``plugin.json`` manifest, stored at
        ``self._manifest_commands`` during loading.  Plugins that need
        dynamic command discovery should override this method.
        """
        raw: list[dict] = getattr(self, "_manifest_commands", [])
        result: list[PluginCommand] = []
        for item in raw:
            params_schema = item.get("parameters", {})
            if not params_schema:
                params_schema = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }
            result.append(
                PluginCommand(
                    name=item["name"],
                    description=item.get("description", ""),
                    parameters=params_schema,
                    handler_name=item.get("handler_name", item["name"]),
                )
            )
        return result

    @abstractmethod
    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        """Execute a command by name with the given arguments.

        Args:
            command_name: Name of the command to run (must be in ``get_commands()``).
            args: Keyword arguments for the command.

        Returns:
            A :class:`ToolResult` with the result content and success flag.
        """
        ...

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def get_config_schema(self) -> list[PluginConfigField]:
        """Return configuration options this plugin exposes.

        The default implementation returns the ``config`` entries from the
        plugin manifest.  Dynamic plugins should override.
        """
        raw: list[dict] = getattr(self, "_manifest_config", [])
        return [
            PluginConfigField(
                key=item["key"],
                label=item.get("label", item["key"]),
                type=item.get("type", "string"),
                default=item.get("default"),
                description=item.get("description", ""),
                options=item.get("options"),
            )
            for item in raw
        ]

    async def set_config(self, values: dict[str, Any]) -> None:
        """Apply configuration values.

        The default stores values on ``self._config``.  Plugins may override
        for validation or side-effects.
        """
        if not hasattr(self, "_config"):
            self._config: dict[str, Any] = {}
        self._config.update(values)
        logger.info("Plugin '%s' config updated: %s", self.name, list(values.keys()))

    async def get_config(self) -> dict[str, Any]:
        """Return the current effective configuration."""
        if not hasattr(self, "_config"):
            self._config: dict[str, Any] = {}
        return dict(self._config)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return a summary of the plugin's current state."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "commands": [c.name for c in self.get_commands()],
            "config_schema": [
                {"key": f.key, "type": f.type, "default": f.default}
                for f in self.get_config_schema()
            ],
        }
