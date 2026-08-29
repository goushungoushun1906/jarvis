"""JARVIS Performance Benchmark Script (Phase 6.6).

Measures response times for key API endpoints over multiple iterations
and prints a summary table with min / max / avg latency.

Usage::

    # Make sure the server is running first:
    #   python -m uvicorn server.main:app --port 18200
    #
    python -m tests.test_performance
    # or
    python server/tests/test_performance.py
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PERF] %(message)s",
    stream=__import__("sys").stdout,
)
logger = logging.getLogger("jarvis.perf")

BASE_URL = "http://127.0.0.1:18200"
ITERATIONS = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _measure(
    label: str,
    method: str,
    url: str,
    json_body: dict[str, Any] | None = None,
    cleanup_url: str | None = None,
    iterations: int = ITERATIONS,
) -> dict[str, float]:
    """Run *iterations* requests and return timing stats (all in ms)."""
    times: list[float] = []

    for i in range(iterations):
        start = time.perf_counter()
        try:
            resp = requests.request(method, f"{BASE_URL}{url}", json=json_body, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Request %d/%d failed for %s: %s", i + 1, iterations, label, exc)
            continue
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Cleanup (e.g. delete a created conversation)
        if cleanup_url and resp.status_code == 200:
            try:
                resp_data = resp.json()
                if isinstance(resp_data, dict):
                    item_id = resp_data.get("id") or resp_data.get("conversation_id")
                    if item_id:
                        requests.delete(f"{BASE_URL}{cleanup_url}/{item_id}", timeout=10)
            except Exception:
                pass

        times.append(elapsed_ms)

    if not times:
        logger.error("All iterations failed for %s — skipping.", label)
        return {"avg": -1.0, "min": -1.0, "max": -1.0}

    return {
        "avg": sum(times) / len(times),
        "min": min(times),
        "max": max(times),
    }


def _print_table(results: list[tuple[str, dict[str, float]]]) -> None:
    """Print a formatted summary table."""
    header = f"{'Endpoint':<30} {'Avg (ms)':>10} {'Min (ms)':>10} {'Max (ms)':>10}"
    sep = "-" * len(header)

    logger.info(sep)
    logger.info(header)
    logger.info(sep)
    for label, stats in results:
        avg_str = f"{stats['avg']:.2f}" if stats["avg"] >= 0 else "N/A"
        min_str = f"{stats['min']:.2f}" if stats["min"] >= 0 else "N/A"
        max_str = f"{stats['max']:.2f}" if stats["max"] >= 0 else "N/A"
        logger.info(f"{label:<30} {avg_str:>10} {min_str:>10} {max_str:>10}")
    logger.info(sep)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmarks() -> None:
    """Execute all endpoint benchmarks and print results."""
    logger.info("Starting JARVIS performance benchmarks …")
    logger.info("Target: %s  |  Iterations: %d", BASE_URL, ITERATIONS)
    logger.info("")

    # Verify server is reachable
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except Exception as exc:
        logger.error("Server not reachable at %s: %s", BASE_URL, exc)
        return

    results: list[tuple[str, dict[str, float]]] = []

    # 1. Health check
    results.append(
        ("GET /health", _measure("GET /health", "GET", "/health")),
    )

    # 2. Config read
    results.append(
        ("GET /api/config", _measure("GET /api/config", "GET", "/api/config")),
    )

    # 3. Plugin list
    results.append(
        ("GET /api/plugins", _measure("GET /api/plugins", "GET", "/api/plugins")),
    )

    # 4. Privacy stats
    results.append(
        ("GET /api/privacy/stats", _measure("GET /api/privacy/stats", "GET", "/api/privacy/stats")),
    )

    # 5. Debug info
    results.append(
        ("GET /api/debug/info", _measure("GET /api/debug/info", "GET", "/api/debug/info")),
    )

    # 6. Conversation write + delete cycle
    results.append(
        (
            "POST+DEL /api/conversations",
            _measure(
                "POST+DEL /api/conversations",
                "POST",
                "/api/conversations",
                json_body={"title": "perf-test"},
                cleanup_url="/api/conversations",
            ),
        ),
    )

    # Print summary
    logger.info("")
    _print_table(results)
    logger.info("PERFORMANCE TEST COMPLETE")


if __name__ == "__main__":
    run_benchmarks()
