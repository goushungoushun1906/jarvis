"""JARVIS Packaging / Build Script (Phase 6.4).

Creates a self-contained ``dist/jarvis/`` directory with everything needed
to run JARVIS in production mode:

    dist/
      jarvis/
        server/                (copied from project)
        static/                (Vite build output)
        config.example.json    (example configuration)
        run.bat                (Windows startup)
        run.sh                 (Linux / macOS startup)
        README.md              (brief usage instructions)

Usage::

    python scripts/build.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent          # jarvis/
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
SERVER_ROOT = PROJECT_ROOT / "server"
DIST_ROOT = PROJECT_ROOT / "dist" / "jarvis"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BUILD] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis.build")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path, label: str) -> None:
    """Run *cmd* inside *cwd*; raise SystemExit on failure."""
    logger.info("Running %s …", label)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("%s failed:\n%s", label, result.stderr)
        sys.exit(1)
    logger.info("%s completed successfully.", label)


def _ensure_clean_dist() -> None:
    """Remove old dist directory and recreate it."""
    if DIST_ROOT.exists():
        logger.info("Cleaning previous dist/ directory …")
        shutil.rmtree(DIST_ROOT)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info("Created %s", DIST_ROOT)


def _copy_server() -> None:
    """Copy the server package into dist (excluding __pycache__)."""
    dest = DIST_ROOT / "server"
    logger.info("Copying server/ → %s", dest)
    shutil.copytree(
        SERVER_ROOT,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "libs"),
    )


def _copy_frontend() -> None:
    """Copy the Vite build output into dist/static."""
    build_output = FRONTEND_ROOT / "dist"
    dest = DIST_ROOT / "static"
    if not build_output.exists():
        logger.error(
            "Frontend build output not found at %s — did vite build succeed?",
            build_output,
        )
        sys.exit(1)
    logger.info("Copying frontend build → %s", dest)
    shutil.copytree(build_output, dest)


def _write_config_example() -> None:
    """Write the example configuration file."""
    config: dict[str, object] = {
        "JARVIS_MODE": "prod",
        "LLM_API_KEY": "your-api-key-here",
        "LLM_MODEL": "agnes-2.0-flash",
        "LLM_BASE_URL": "https://api.example.com/v1",
        "SERVER_PORT": 18200,
    }
    path = DIST_ROOT / "config.example.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote %s", path)


def _write_run_bat() -> None:
    """Write the Windows startup batch script."""
    content = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "set JARVIS_MODE=prod\r\n"
        "python -m uvicorn server.main:app --host 0.0.0.0 --port 18200\r\n"
    )
    path = DIST_ROOT / "run.bat"
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote %s", path)


def _write_run_sh() -> None:
    """Write the Linux / macOS startup shell script."""
    content = (
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        "export JARVIS_MODE=prod\n"
        "python -m uvicorn server.main:app --host 0.0.0.0 --port 18200\n"
    )
    path = DIST_ROOT / "run.sh"
    path.write_text(content, encoding="utf-8")
    # Mark executable (no-op on Windows but harmless)
    path.chmod(path.stat().st_mode | 0o755)
    logger.info("Wrote %s", path)


def _write_readme() -> None:
    """Write a brief README for the distribution package."""
    content = (
        "# JARVIS — Distribution Package\n\n"
        "## Quick Start\n\n"
        "1. Copy `config.example.json` to `config.json` and fill in your settings.\n"
        "2. Install dependencies: `pip install fastapi uvicorn`\n"
        "3. Run the server:\n"
        "   - **Windows**: double-click `run.bat`\n"
        "   - **Linux / macOS**: `chmod +x run.sh && ./run.sh`\n\n"
        "4. Open http://localhost:18200 in your browser.\n\n"
        "## Configuration\n\n"
        "See `config.example.json` for all available options.\n"
        "You can set environment variables instead of using the config file.\n"
    )
    path = DIST_ROOT / "README.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote %s", path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> NoReturn:
    """Execute the full build pipeline."""
    logger.info("=== JARVIS Build Pipeline ===")

    # Step 1 — Frontend build
    _run(
        ["npx", "vite", "build"],
        cwd=FRONTEND_ROOT,
        label="Frontend Vite build",
    )

    # Step 2 — Prepare dist directory
    _ensure_clean_dist()

    # Step 3 — Copy artefacts
    _copy_server()
    _copy_frontend()

    # Step 4 — Write packaging files
    _write_config_example()
    _write_run_bat()
    _write_run_sh()
    _write_readme()

    logger.info("=== Build complete — output in %s ===", DIST_ROOT)
    sys.exit(0)


if __name__ == "__main__":
    main()
