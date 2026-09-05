"""JARVIS Backend Server — modular entry point."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# In PyInstaller production: sys._MEIPASS points to the extracted bundle.
# The .env and jarvis.db live in the resources/backend/ folder in the packaged app.
if getattr(sys, 'frozen', False):
    # The exe is at: _MEIPASS/dist/backend.exe
    # We want: _MEIPASS/ (the server root)
    _meipass = getattr(sys, '_MEIPASS', None)
    if _meipass:
        _server_dir = Path(_meipass)
    else:
        # Fallback: exe parent
        _server_dir = Path(sys.executable).parent
    os.chdir(str(_server_dir))
    # Ensure the server dir is at the front of sys.path so 'main' is importable
    _spd = str(_server_dir)
    if _spd not in sys.path:
        sys.path.insert(0, _spd)

# Add project libs directory to Python path (for vosk and other local deps).
# Append at the end so system/site packages take precedence and avoid version
# conflicts with vendored libraries (e.g. sentence-transformers, huggingface_hub).
_libs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")
if os.path.isdir(_libs_dir) and _libs_dir not in sys.path:
    sys.path.append(_libs_dir)

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import database as db
from app.config import settings
from app.models import HealthResponse
from app.routes import chat as chat_router
from app.routes import config as config_router
from app.routes import costs as costs_router
from app.routes.memory import router as memory_router
from app.routes.tools import router as tools_router
from app.routes.voice import router as voice_router
from app.routes.plugins import router as plugins_router
from app.routes.privacy import router as privacy_router
from app.routes.update import router as update_router
from app.routes.wakeword import router as wakeword_router
from app.routes.scheduler import router as scheduler_router
from app.routes.skills import router as skills_router
from app.debug import debug_middleware, debug_router, env_config

logging.basicConfig(
    level=getattr(logging, env_config.log_level.value, logging.INFO),
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis")
logger.info("JARVIS environment mode=%s debug=%s", env_config.mode, env_config.debug)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("JARVIS server starting — model: %s", settings.llm_model)
    await db.init_db()

    from app import vector_memory
    try:
        await vector_memory.init_vector_memory()
    except Exception as exc:
        logger.warning("Vector memory initialization failed: %s", exc)

    from app.builtins import register_tools
    register_tools()

    from app.browser_tools import register_browser_tools
    register_browser_tools()

    from app.mcp_client import mcp_client
    from app.mcp_adapter import register_mcp_tools
    from app.tools import registry as tool_registry

    mcp_client.load_config()
    await mcp_client.initialize()
    register_mcp_tools(tool_registry)

    from app.models import ModelConfig
    model_raw = await db.get_setting("model_config")
    if model_raw:
        try:
            mc = ModelConfig.model_validate_json(model_raw)
            settings.llm_model = mc.model
            settings.llm_base_url = mc.base_url
            logger.info("Loaded model config from DB: model=%s", mc.model)
        except Exception:
            logger.warning("Failed to parse stored model config, using env defaults")

    active_id = await db.get_setting("active_model_profile")
    if active_id:
        await db.switch_model_profile(active_id)

    from app.plugins.manager import manager as plugin_manager

    async def _on_plugin_state(name: str, enabled: bool) -> None:
        await db.set_plugin_enabled_state(name, enabled)

    plugin_manager.on_state_changed(_on_plugin_state)

    all_settings = await db.get_all_plugin_settings()
    auto_enable = []
    for s in all_settings:
        if not s["enabled"]:
            continue
        auto_enable.append(s["plugin_name"])

    await plugin_manager.scan_and_load(auto_enable=auto_enable)

    for s in all_settings:
        if not s.get("config"):
            continue
        instance = plugin_manager.registry.get_plugin(s["plugin_name"])
        if instance is None:
            continue
        try:
            await instance.set_config(json.loads(s["config"]))
        except Exception as exc:
            logger.warning("Failed to restore config for plugin '%s': %s", s["plugin_name"], exc)

    logger.info("Plugin system initialised — %d plugins loaded", len(plugin_manager.get_all_plugin_info()))

    yield

    from app.mcp_client import mcp_client
    try:
        await mcp_client.shutdown()
    except Exception as exc:
        logger.warning("Error shutting down MCP clients: %s", exc)

    await db.close_db()
    logger.info("JARVIS server shutting down")


app = FastAPI(
    title="JARVIS Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if env_config.debug:
    app.add_middleware(debug_middleware)

app.include_router(chat_router.router)
app.include_router(config_router.router)
app.include_router(costs_router.router)
app.include_router(memory_router)
app.include_router(tools_router)
app.include_router(voice_router)
app.include_router(plugins_router)
app.include_router(privacy_router)
app.include_router(update_router)
app.include_router(wakeword_router)
app.include_router(scheduler_router)
app.include_router(skills_router)
app.include_router(debug_router)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.server_port,
        log_level="info",
    )
