"""Entry point for `python -m evals.run`."""

from __future__ import annotations

import asyncio
import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
