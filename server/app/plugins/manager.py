"""Plugin manager — scans plugin directories, manages lifecycle."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .base import BasePlugin
from .registry import registry as _reg

logger = logging.getLogger("jarvis")

# Default directory where plugins are installed
DEFAULT_PLUGINS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins"
)


# ---------------------------------------------------------------------------
# Plugin Manager
# ---------------------------------------------------------------------------


class PluginManager:
    """High-level manager that orchestrates plugin scanning and lifecycle.

    Typically you create one instance and call :meth:`scan_and_load` at
    startup.  The manager also persists enable/disable state to the database
    via callbacks.
    """

    def __init__(self, plugins_dir: str | None = None) -> None:
        self.plugins_dir = plugins_dir or DEFAULT_PLUGINS_DIR
        self._on_state_changed: list[callable] = []

    @property
    def registry(self):
        return _reg

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_plugins(self) -> list[dict]:
        """Scan the plugins directory and return manifests of available plugins.

        Returns a list of dicts with keys: ``name, version, description, author,
        manifest_path, plugin_dir``.
        """
        result: list[dict] = []
        if not os.path.isdir(self.plugins_dir):
            logger.warning("Plugins directory not found: %s", self.plugins_dir)
            return result

        for entry in os.scandir(self.plugins_dir):
            if not entry.is_dir():
                continue
            manifest_path = os.path.join(entry.path, "plugin.json")
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping %s — bad plugin.json: %s", entry.name, e)
                continue

            result.append(
                {
                    "name": manifest.get("name", entry.name),
                    "version": manifest.get("version", "0.1.0"),
                    "description": manifest.get("description", ""),
                    "author": manifest.get("author", ""),
                    "manifest_path": manifest_path,
                    "plugin_dir": entry.path,
                }
            )
        return result

    async def scan_and_load(
        self,
        auto_enable: list[str] | None = None,
    ) -> dict[str, BasePlugin]:
        """Discover all plugins on disk and load them.

        Args:
            auto_enable: Optional list of plugin names to enable after loading.
                         If None, all discovered plugins are enabled.

        Returns:
            Dict mapping plugin name to instance.
        """
        discovered = self.discover_plugins()
        loaded: dict[str, BasePlugin] = {}

        for info in discovered:
            instance = await _reg.load_plugin(info["plugin_dir"])
            if instance is None:
                continue
            # Store the plugin dir so reload works
            instance._plugin_dir = info["plugin_dir"]
            loaded[instance.name] = instance

            # Auto-enable?
            should_enable = True
            if auto_enable is not None:
                should_enable = instance.name in auto_enable
            if should_enable:
                await _reg.enable_plugin(instance.name)

        logger.info(
            "Plugin scan complete: %d discovered, %d loaded",
            len(discovered),
            len(loaded),
        )
        return loaded

    # ------------------------------------------------------------------
    # Enable / disable with persistence callback
    # ------------------------------------------------------------------

    def on_state_changed(self, callback: callable) -> None:
        """Register a callback ``callback(plugin_name, enabled: bool)``
        that is invoked whenever a plugin is enabled or disabled."""
        self._on_state_changed.append(callback)

    async def enable_plugin(self, name: str) -> bool:
        ok = await _reg.enable_plugin(name)
        if ok:
            await self._notify_state(name, True)
        return ok

    async def disable_plugin(self, name: str) -> bool:
        ok = await _reg.disable_plugin(name)
        if ok:
            await self._notify_state(name, False)
        return ok

    async def _notify_state(self, name: str, enabled: bool) -> None:
        for cb in self._on_state_changed:
            try:
                result = cb(name, enabled)
                if result is not None:
                    await result
            except Exception as e:
                logger.warning("State change callback error: %s", e)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_plugin_info(self, name: str) -> dict | None:
        """Get detailed info for a single plugin."""
        instance = _reg.get_plugin(name)
        if instance is None:
            return None
        return {
            "name": instance.name,
            "version": instance.version,
            "description": instance.description,
            "author": instance.author,
            "enabled": _reg.is_plugin_enabled(name),
            "commands": [
                {
                    "name": c.name,
                    "description": c.description,
                    "parameters": c.parameters,
                }
                for c in instance.get_commands()
            ],
            "config_schema": [
                {
                    "key": f.key,
                    "label": f.label,
                    "type": f.type,
                    "default": f.default,
                    "description": f.description,
                    "options": f.options,
                }
                for f in instance.get_config_schema()
            ],
        }

    def get_all_plugin_info(self) -> list[dict]:
        """Get summary info for all loaded plugins."""
        return _reg.list_plugins()


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

manager = PluginManager()
