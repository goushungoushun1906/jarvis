"""Individual evaluation cases for the JARVIS backend.

Each case is a small async function that exercises one piece of functionality
and returns an EvalResult. Cases are discovered automatically by EvalRunner.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Coroutine

import httpx

from app import database as db


@dataclass
class EvalResult:
    """Outcome of a single evaluation case."""

    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0.0
    details: dict = field(default_factory=dict)


EvalCase = Callable[[httpx.AsyncClient, str], Coroutine[None, None, EvalResult]]


async def eval_health(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Backend health endpoint returns status ok."""
    start = time.perf_counter()
    r = await client.get(f"{base}/health", timeout=10)
    duration = (time.perf_counter() - start) * 1000
    data = r.json()
    passed = r.status_code == 200 and data.get("status") == "ok"
    return EvalResult(
        name="health",
        passed=passed,
        message="Health endpoint OK" if passed else f"Unexpected response: {data}",
        duration_ms=duration,
    )


async def eval_config(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Config endpoint returns model configuration."""
    start = time.perf_counter()
    r = await client.get(f"{base}/api/config", timeout=10)
    duration = (time.perf_counter() - start) * 1000
    data = r.json()
    passed = r.status_code == 200 and "model_config" in data
    return EvalResult(
        name="config",
        passed=passed,
        message="Config endpoint OK" if passed else f"Missing model_config: {data}",
        duration_ms=duration,
    )


async def eval_plugins_list(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Plugin list endpoint returns an array."""
    start = time.perf_counter()
    r = await client.get(f"{base}/api/plugins", timeout=10)
    duration = (time.perf_counter() - start) * 1000
    data = r.json()
    plugins = data.get("plugins") if isinstance(data, dict) else data
    passed = r.status_code == 200 and isinstance(plugins, list)
    return EvalResult(
        name="plugins_list",
        passed=passed,
        message=f"Found {len(plugins or [])} plugins" if passed else f"Bad response: {data}",
        duration_ms=duration,
    )


async def eval_memory_crud(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Create, search and delete a memory through the API."""
    start = time.perf_counter()
    content = f"eval-test-memory-{uuid.uuid4().hex[:8]}"
    create_r = await client.post(
        f"{base}/api/memories",
        json={"content": content, "category": "general", "importance": 0.5},
        timeout=10,
    )
    if create_r.status_code not in (200, 201):
        return EvalResult(
            name="memory_crud",
            passed=False,
            message=f"Create failed: {create_r.status_code} {create_r.text}",
        )
    memory_id = create_r.json().get("id")

    search_r = await client.get(f"{base}/api/memories/search", params={"q": content}, timeout=10)
    search_ok = search_r.status_code == 200 and any(
        m.get("content") == content for m in search_r.json().get("results", [])
    )

    delete_r = await client.delete(f"{base}/api/memories/{memory_id}", timeout=10)
    passed = search_ok and delete_r.status_code == 200
    duration = (time.perf_counter() - start) * 1000
    return EvalResult(
        name="memory_crud",
        passed=passed,
        message="Memory CRUD OK" if passed else f"search_ok={search_ok} delete={delete_r.status_code}",
        duration_ms=duration,
    )


async def eval_memory_semantic(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Semantic memory search returns results for a related query."""
    start = time.perf_counter()
    content = f"I love python programming {uuid.uuid4().hex[:8]}"
    create_r = await client.post(
        f"{base}/api/memories",
        json={"content": content, "category": "preference", "importance": 0.8},
        timeout=10,
    )
    if create_r.status_code not in (200, 201):
        return EvalResult(
            name="memory_semantic",
            passed=False,
            message=f"Create failed: {create_r.status_code}",
        )
    memory_id = create_r.json().get("id")

    # Give the vector index a moment to settle
    await asyncio.sleep(0.5)
    search_r = await client.get(
        f"{base}/api/memories/semantic",
        params={"q": "coding with Python", "limit": 5},
        timeout=15,
    )
    results = search_r.json().get("results", [])
    found = any(str(memory_id) == str(r.get("memory_id")) for r in results)

    await client.delete(f"{base}/api/memories/{memory_id}", timeout=10)
    duration = (time.perf_counter() - start) * 1000
    passed = search_r.status_code == 200 and found
    return EvalResult(
        name="memory_semantic",
        passed=passed,
        message="Semantic recall OK" if passed else f"Memory not in top results: {results}",
        duration_ms=duration,
    )


async def eval_chat_non_stream(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Non-streaming chat returns an assistant reply."""
    start = time.perf_counter()
    r = await client.post(
        f"{base}/api/chat",
        json={"messages": [{"role": "user", "content": "你好"}]},
        timeout=60,
    )
    duration = (time.perf_counter() - start) * 1000
    data = r.json()
    passed = r.status_code == 200 and bool(data.get("response") or data.get("content"))
    return EvalResult(
        name="chat_non_stream",
        passed=passed,
        message="Chat returned a reply" if passed else f"Bad chat response: {data}",
        duration_ms=duration,
    )


async def eval_costs_summary(client: httpx.AsyncClient, base: str) -> EvalResult:
    """Cost summary endpoint returns aggregate statistics."""
    start = time.perf_counter()
    r = await client.get(f"{base}/api/costs/summary", timeout=10)
    duration = (time.perf_counter() - start) * 1000
    data = r.json()
    passed = r.status_code == 200 and "total_calls" in data
    return EvalResult(
        name="costs_summary",
        passed=passed,
        message="Cost summary OK" if passed else f"Bad response: {data}",
        duration_ms=duration,
    )


# Public registry of all eval cases
ALL_CASES: list[EvalCase] = [
    eval_health,
    eval_config,
    eval_plugins_list,
    eval_memory_crud,
    eval_memory_semantic,
    eval_chat_non_stream,
    eval_costs_summary,
]
