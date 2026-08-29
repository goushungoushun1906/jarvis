"""JARVIS Plugin API routes — list, enable, disable, reload, install, uninstall, config plugins."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile

from fastapi import APIRouter, HTTPException, UploadFile, File

from ..plugins.manager import manager as plugin_manager

logger = logging.getLogger("jarvis")

router = APIRouter(prefix="/api", tags=["plugins"])


# ------------------------------------------------------------------
# GET /api/plugins — list all plugins with status
# ------------------------------------------------------------------

@router.get("/plugins")
async def api_list_plugins():
    """List all loaded plugins and their current status."""
    return plugin_manager.get_all_plugin_info()


# ------------------------------------------------------------------
# GET /api/plugins/discover — scan plugins directory (no load)
# NOTE: Must be registered BEFORE /plugins/{name} to avoid being
# captured by the dynamic route.
# ------------------------------------------------------------------

@router.get("/plugins/discover")
async def api_discover_plugins():
    """Scan the plugins directory and return what is available on disk."""
    return plugin_manager.discover_plugins()


# ------------------------------------------------------------------
# POST /api/plugins/install — upload a plugin ZIP
# NOTE: Must be registered BEFORE /plugins/{name}/... routes.
# ------------------------------------------------------------------

@router.post("/plugins/install")
async def api_install_plugin(zip_file: UploadFile = File(...)):
    """Install a plugin from a uploaded ZIP file.

    The ZIP must contain a directory with plugin.json and main.py at its root.
    Example structure:
        my_plugin/
          plugin.json
          main.py
    """
    if not zip_file.filename or not zip_file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files are accepted for plugin installation")

    plugins_dir = plugin_manager.plugins_dir
    os.makedirs(plugins_dir, exist_ok=True)

    # Save ZIP to temp file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        content = await zip_file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            # Validate: must contain at least one plugin.json
            plugin_json_entries = [e for e in zf.namelist() if e.endswith("plugin.json")]
            if not plugin_json_entries:
                raise HTTPException(400, "ZIP does not contain a plugin.json file")

            # Use the directory containing plugin.json as the plugin name
            plugin_json_path = plugin_json_entries[0]
            plugin_dir_name = os.path.dirname(plugin_json_path).replace("\\", "/")
            # Handle flat ZIP (plugin.json at root)
            if not plugin_dir_name:
                plugin_dir_name = os.path.splitext(zip_file.filename)[0]
            else:
                plugin_dir_name = plugin_dir_name.split("/")[0]

            target_dir = os.path.join(plugins_dir, plugin_dir_name)

            # Check if plugin already exists
            if os.path.exists(target_dir):
                raise HTTPException(409, f"Plugin '{plugin_dir_name}' already exists. Uninstall it first.")

            # Must also contain main.py
            main_py_entries = [
                e for e in zf.namelist()
                if e.startswith(plugin_dir_name + "/") and e.endswith("main.py")
            ]
            if not main_py_entries:
                raise HTTPException(400, "ZIP does not contain a main.py file")

            # Extract
            os.makedirs(target_dir, exist_ok=True)
            for entry in zf.namelist():
                if entry.startswith(plugin_dir_name + "/") and not entry.endswith("/"):
                    dest = os.path.join(target_dir, os.path.basename(entry))
                    with zf.open(entry) as src, open(dest, "wb") as dst:
                        dst.write(src.read())

        # Load the newly installed plugin
        instance = await plugin_manager.registry.load_plugin(target_dir)
        if instance is None:
            # Clean up failed install
            shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(500, "Plugin loaded but failed to initialize")

        instance._plugin_dir = target_dir
        await plugin_manager.enable_plugin(plugin_dir_name)

        logger.info("Plugin installed and enabled: %s", plugin_dir_name)
        return {
            "status": "ok",
            "plugin": instance.name,
            "version": instance.version,
            "commands": [c.name for c in instance.get_commands()],
        }

    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid ZIP file")
    except Exception as e:
        logger.error("Plugin install error: %s", e)
        raise HTTPException(500, f"Plugin installation failed: {str(e)}")
    finally:
        os.unlink(tmp_path)


# ------------------------------------------------------------------
# GET /api/plugins/{name} — get plugin detail
# ------------------------------------------------------------------

@router.get("/plugins/{name}")
async def api_get_plugin(name: str):
    """Get detailed info for a single plugin."""
    info = plugin_manager.get_plugin_info(name)
    if info is None:
        raise HTTPException(404, f"Plugin not found: {name}")
    return info


# ------------------------------------------------------------------
# POST /api/plugins/{name}/enable
# ------------------------------------------------------------------

@router.post("/plugins/{name}/enable")
async def api_enable_plugin(name: str):
    """Enable a loaded plugin so its commands become available."""
    ok = await plugin_manager.enable_plugin(name)
    if not ok:
        raise HTTPException(404, f"Plugin not found or already enabled: {name}")
    return {"status": "ok", "plugin": name, "enabled": True}


# ------------------------------------------------------------------
# POST /api/plugins/{name}/disable
# ------------------------------------------------------------------

@router.post("/plugins/{name}/disable")
async def api_disable_plugin(name: str):
    """Disable a plugin, hiding its commands."""
    ok = await plugin_manager.disable_plugin(name)
    if not ok:
        raise HTTPException(404, f"Plugin not found or already disabled: {name}")
    return {"status": "ok", "plugin": name, "enabled": False}


# ------------------------------------------------------------------
# POST /api/plugins/{name}/reload
# ------------------------------------------------------------------

@router.post("/plugins/{name}/reload")
async def api_reload_plugin(name: str):
    """Reload a plugin — unload then re-load from disk."""
    instance = await plugin_manager.registry.reload_plugin(name)
    if instance is None:
        raise HTTPException(500, f"Failed to reload plugin: {name}")
    return {
        "status": "ok",
        "plugin": instance.name,
        "version": instance.version,
        "commands": [c.name for c in instance.get_commands()],
    }


# ------------------------------------------------------------------
# POST /api/plugins/{name}/uninstall
# ------------------------------------------------------------------

@router.post("/plugins/{name}/uninstall")
async def api_uninstall_plugin(name: str):
    """Uninstall a plugin — unload, then remove its directory from disk."""
    instance = plugin_manager.registry.get_plugin(name)
    if instance is None:
        raise HTTPException(404, f"Plugin not found: {name}")

    plugin_dir = getattr(instance, "_plugin_dir", None)
    if plugin_dir is None:
        raise HTTPException(500, f"Cannot determine plugin directory for: {name}")

    # Unload first
    await plugin_manager.registry.unload_plugin(name)

    # Remove from disk
    try:
        shutil.rmtree(plugin_dir)
    except OSError as e:
        logger.error("Failed to remove plugin directory %s: %s", plugin_dir, e)
        raise HTTPException(500, f"Failed to remove plugin files: {str(e)}")

    logger.info("Plugin uninstalled and removed: %s", name)
    return {"status": "ok", "plugin": name, "uninstalled": True}


# ------------------------------------------------------------------
# GET /api/plugins/{name}/config — get current config values
# ------------------------------------------------------------------

@router.get("/plugins/{name}/config")
async def api_get_plugin_config(name: str):
    """Get the current configuration for a plugin."""
    instance = plugin_manager.registry.get_plugin(name)
    if instance is None:
        raise HTTPException(404, f"Plugin not found: {name}")

    schema = instance.get_config_schema()
    values = await instance.get_config()

    return {
        "plugin": name,
        "schema": [
            {
                "key": f.key,
                "label": f.label,
                "type": f.type,
                "default": f.default,
                "description": f.description,
                "options": f.options,
                "value": values.get(f.key, f.default),
            }
            for f in schema
        ],
    }


# ------------------------------------------------------------------
# PUT /api/plugins/{name}/config — update config values
# ------------------------------------------------------------------

@router.put("/plugins/{name}/config")
async def api_update_plugin_config(name: str, body: dict):
    """Update configuration values for a plugin.

    Body should be a dict mapping config keys to their new values.
    Example: {"default_timezone": "America/New_York"}
    """
    instance = plugin_manager.registry.get_plugin(name)
    if instance is None:
        raise HTTPException(404, f"Plugin not found: {name}")

    # Validate keys against schema
    schema = instance.get_config_schema()
    valid_keys = {f.key for f in schema}
    invalid_keys = set(body.keys()) - valid_keys
    if invalid_keys:
        raise HTTPException(400, f"Unknown config keys: {', '.join(invalid_keys)}")

    # Update config
    await instance.set_config(body)

    logger.info("Plugin config updated: %s — keys: %s", name, list(body.keys()))
    return {"status": "ok", "plugin": name}
