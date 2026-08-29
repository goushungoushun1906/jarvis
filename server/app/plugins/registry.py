"""Plugin registry — loads, unloads, queries plugins and dispatches commands."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from typing import Any

from .base import BasePlugin, PluginCommand, ToolResult

logger = logging.getLogger("jarvis")


class PluginRegistry:
    """Central registry that manages all loaded plugins.

    Plugins are keyed by name.  Commands from all **enabled** plugins are
    aggregated and can be dispatched via :meth:`execute_command`.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._plugin_instances: dict[str, BasePlugin] = {}  # loaded instances
        self._enabled: set[str] = set()
        self._command_map: dict[str, tuple[str, str]] = {}
        # command_name -> (plugin_name, handler_name)

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    async def load_plugin(self, plugin_dir: str) -> BasePlugin | None:
        """Load a plugin from a directory containing ``plugin.json`` and ``main.py``.

        Args:
            plugin_dir: Absolute path to the plugin directory.

        Returns:
            The plugin instance if loaded successfully, or ``None`` on failure.
        """
        manifest_path = os.path.join(plugin_dir, "plugin.json")
        main_path = os.path.join(plugin_dir, "main.py")

        if not os.path.isfile(manifest_path):
            logger.warning("No plugin.json found in %s", plugin_dir)
            return None
        if not os.path.isfile(main_path):
            logger.warning("No main.py found in %s", plugin_dir)
            return None

        # Read manifest
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read plugin.json in %s: %s", plugin_dir, e)
            return None

        name = manifest.get("name", os.path.basename(plugin_dir))
        version = manifest.get("version", "0.1.0")
        description = manifest.get("description", "")
        author = manifest.get("author", "")

        if name in self._plugin_instances:
            logger.warning("Plugin '%s' is already loaded, unloading first", name)
            await self.unload_plugin(name)

        # Dynamically import the main module
        module_name = f"_jarvis_plugin_{name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, main_path)
            if spec is None or spec.loader is None:
                logger.error("Cannot load module spec for %s", main_path)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error("Failed to import plugin module '%s': %s", name, e)
            return None

        # Find the plugin class (subclass of BasePlugin)
        plugin_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BasePlugin)
                and attr is not BasePlugin
            ):
                plugin_class = attr
                break

        if plugin_class is None:
            logger.error("No BasePlugin subclass found in %s", main_path)
            return None

        # Instantiate and inject manifest data
        instance = plugin_class()
        instance.name = name
        instance.version = version
        instance.description = description
        instance.author = author
        instance._manifest_commands = manifest.get("commands", [])
        instance._manifest_config = manifest.get("config", [])

        # Run on_load hook
        try:
            await instance.on_load()
        except Exception as e:
            logger.error("Plugin '%s' on_load failed: %s", name, e)
            return None

        self._plugin_instances[name] = instance

        # Register commands from the plugin
        self._register_commands(name, instance)

        logger.info(
            "Plugin loaded: %s v%s (%d commands)",
            name,
            version,
            len(instance.get_commands()),
        )
        return instance

    async def unload_plugin(self, name: str) -> bool:
        """Unload a plugin by name.

        Disables it first if enabled, then removes from registry.

        Returns ``True`` if the plugin was found and unloaded.
        """
        instance = self._plugin_instances.get(name)
        if instance is None:
            return False

        if name in self._enabled:
            await self._disable_plugin_instance(name, instance)

        try:
            await instance.on_unload()
        except Exception as e:
            logger.warning("Plugin '%s' on_unload error: %s", name, e)

        self._unregister_commands(name)
        del self._plugin_instances[name]
        logger.info("Plugin unloaded: %s", name)
        return True

    async def reload_plugin(self, name: str) -> BasePlugin | None:
        """Reload a plugin: unload then reload from its original directory.

        Returns the new instance or ``None`` on failure.
        """
        instance = self._plugin_instances.get(name)
        if instance is None:
            logger.warning("Cannot reload unknown plugin: %s", name)
            return None

        plugin_dir = getattr(instance, "_plugin_dir", None)
        if plugin_dir is None:
            logger.warning("No _plugin_dir stored for '%s', cannot reload", name)
            return None

        await self.unload_plugin(name)
        return await self.load_plugin(plugin_dir)

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    async def enable_plugin(self, name: str) -> bool:
        """Enable a loaded plugin so its commands become available.

        Returns ``True`` on success, ``False`` if the plugin is not found.
        """
        instance = self._plugin_instances.get(name)
        if instance is None:
            return False
        if name in self._enabled:
            return True  # already enabled

        try:
            await instance.on_enable()
        except Exception as e:
            logger.error("Plugin '%s' on_enable failed: %s", name, e)
            return False

        self._enabled.add(name)
        logger.info("Plugin enabled: %s", name)
        return True

    async def disable_plugin(self, name: str) -> bool:
        """Disable a plugin so its commands are hidden.

        Returns ``True`` on success, ``False`` if not found.
        """
        instance = self._plugin_instances.get(name)
        if instance is None:
            return False
        if name not in self._enabled:
            return True  # already disabled

        return await self._disable_plugin_instance(name, instance)

    async def _disable_plugin_instance(self, name: str, instance: BasePlugin) -> bool:
        try:
            await instance.on_disable()
        except Exception as e:
            logger.warning("Plugin '%s' on_disable error: %s", name, e)
        self._enabled.discard(name)
        logger.info("Plugin disabled: %s", name)
        return True

    # ------------------------------------------------------------------
    # Command management
    # ------------------------------------------------------------------

    def _register_commands(self, plugin_name: str, instance: BasePlugin) -> None:
        for cmd in instance.get_commands():
            full_name = f"{plugin_name}_{cmd.name}" if plugin_name not in cmd.name else cmd.name
            self._command_map[cmd.name] = (plugin_name, cmd.handler_name)
            logger.debug("  command: %s → %s.%s", cmd.name, plugin_name, cmd.handler_name)

    def _unregister_commands(self, plugin_name: str) -> None:
        to_remove = [
            cmd_name
            for cmd_name, (pn, _) in self._command_map.items()
            if pn == plugin_name
        ]
        for cmd_name in to_remove:
            del self._command_map[cmd_name]

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_plugin(self, name: str) -> BasePlugin | None:
        """Return a loaded plugin instance by name."""
        return self._plugin_instances.get(name)

    def list_plugins(self) -> list[dict]:
        """Return summary info for all loaded plugins."""
        result: list[dict] = []
        for name, inst in self._plugin_instances.items():
            result.append(
                {
                    "name": inst.name,
                    "version": inst.version,
                    "description": inst.description,
                    "author": inst.author,
                    "enabled": name in self._enabled,
                    "commands": [c.name for c in inst.get_commands()],
                    "config_schema": [
                        {"key": f.key, "type": f.type, "default": f.default}
                        for f in inst.get_config_schema()
                    ],
                }
            )
        return result

    def is_plugin_enabled(self, name: str) -> bool:
        return name in self._enabled

    # ------------------------------------------------------------------
    # Command aggregation & dispatch
    # ------------------------------------------------------------------

    def get_all_commands(self) -> list[dict]:
        """Aggregate OpenAI tool schemas from all enabled plugins."""
        tools: list[dict] = []
        for name, inst in self._plugin_instances.items():
            if name not in self._enabled:
                continue
            for cmd in inst.get_commands():
                tools.append(cmd.to_openai_tool())
        return tools

    async def execute_command(
        self,
        plugin_name: str,
        command_name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        """Dispatch a command to the correct plugin.

        Args:
            plugin_name: Name of the plugin.
            command_name: Name of the command to execute.
            args: Keyword arguments for the command.

        Returns:
            The result of the command execution.
        """
        instance = self._plugin_instances.get(plugin_name)
        if instance is None:
            return ToolResult(
                f"Plugin not found: {plugin_name}", success=False
            )
        if plugin_name not in self._enabled:
            return ToolResult(
                f"Plugin '{plugin_name}' is disabled", success=False
            )
        try:
            return await instance.execute(command_name, args)
        except Exception as e:
            logger.error(
                "Plugin '%s' command '%s' error: %s",
                plugin_name,
                command_name,
                e,
            )
            return ToolResult(
                f"Plugin error: {str(e)}", success=False
            )

    def find_command_plugin(self, command_name: str) -> str | None:
        """Find which plugin owns a command. Returns plugin name or None."""
        entry = self._command_map.get(command_name)
        if entry is None:
            return None
        return entry[0]

    def find_command_handler(self, command_name: str) -> str | None:
        """Find the handler name for a command. Returns handler name or None."""
        entry = self._command_map.get(command_name)
        if entry is None:
            return None
        return entry[1]


# ---------------------------------------------------------------------------
# Global singleton registry
# ---------------------------------------------------------------------------

registry = PluginRegistry()
