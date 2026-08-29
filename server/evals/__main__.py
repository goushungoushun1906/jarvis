"""Allow running evals as a module: `python -m evals`."""

from __future__ import annotations

import asyncio
import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
