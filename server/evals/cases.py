"""Individual evaluation cases for the JARVIS backend.

Each case is a small async function that exercises one piece of functionality
and returns an EvalResult. Cases are discovered automatically by EvalRunner.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Coroutine

import httpx

from app import database as db  # noqa: F401  (kept: cases may poke the DB directly)


# Streaming eval tuning knobs -------------------------------------------------

#: Prompts that reliably make the agent reach for a tool. The first one that
#: produces a tool_call/tool_result pair wins, so a single model hiccup does not
#: fail the whole regression guard.
TOOL_ELICITING_PROMPTS: list[str] = [
    "现在北京时间几点了？请调用工具获取准确时间。",
    "查看当前系统的 CPU 和内存占用情况",
]

#: Upper bound for "time to first text delta". Generous on purpose: this is a
#: regression guard against accidentally blocking the stream, not a p99 SLO.
FIRST_TOKEN_BUDGET_MS = 30_000.0

#: Hard ceiling for one streaming eval request.
STREAM_TIMEOUT_S = 120.0


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


# ---------------------------------------------------------------------------
# Phase 17.6 — streaming regression guards
# ---------------------------------------------------------------------------


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict]]:
    """Yield ``(event_name, payload)`` pairs from an SSE response.

    The backend emits each frame as ``event: <name>`` followed by ``data: <json>``
    (see ``app.routes.chat._sse``). ``data: [DONE]`` is surfaced as the synthetic
    event name ``__done__``; heartbeat comments (``: ping``) are skipped.
    """
    pending_event: str | None = None
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            pending_event = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            if raw == "[DONE]":
                yield "__done__", {}
                pending_event = None
                continue
            try:
                payload: Any = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"__raw__": raw}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            yield (pending_event or "message"), payload
            pending_event = None


async def _stream_chat(
    client: httpx.AsyncClient,
    base: str,
    prompt: str,
) -> tuple[list[tuple[str, dict]], float]:
    """POST one prompt to ``/api/chat/stream`` and collect every SSE event.

    Returns ``(events, elapsed_ms)``. Raises on transport-level failures so the
    runner records them as unhandled exceptions.
    """
    events: list[tuple[str, dict]] = []
    start = time.perf_counter()
    async with client.stream(
        "POST",
        f"{base}/api/chat/stream",
        json={"messages": [{"role": "user", "content": prompt}]},
        timeout=STREAM_TIMEOUT_S,
    ) as response:
        response.raise_for_status()
        async for event in _iter_sse_events(response):
            events.append(event)
    return events, (time.perf_counter() - start) * 1000


async def eval_chat_stream_emits_tool_events(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """A tool-using question emits paired tool_call / tool_result SSE frames.

    Exercises the real Phase 17.3 contract end to end: the agent must announce a
    tool invocation before it runs, report the outcome afterwards, and the two
    frames must be joinable on ``id``.
    """
    start = time.perf_counter()
    last_attempt: dict[str, Any] = {}

    for prompt in TOOL_ELICITING_PROMPTS:
        try:
            events, elapsed = await _stream_chat(client, base, prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced as a case failure
            last_attempt = {"prompt": prompt, "error": f"{type(exc).__name__}: {exc}"}
            continue

        calls = [p.get("tool_call", {}) for name, p in events if name == "tool_call"]
        results = [p.get("tool_result", {}) for name, p in events if name == "tool_result"]
        kinds = [name for name, _ in events]
        last_attempt = {
            "prompt": prompt,
            "tools": [c.get("tool") for c in calls],
            "events": kinds,
        }

        if not calls or not results:
            continue

        # Contract checks on the frames we actually received.
        call_ids = {c.get("id") for c in calls}
        problems: list[str] = []
        if not all(c.get("tool") for c in calls):
            problems.append("tool_call missing 'tool'")
        if not all(c.get("id") for c in calls):
            problems.append("tool_call missing 'id'")
        if not all("success" in r for r in results):
            problems.append("tool_result missing 'success'")
        if not call_ids & {r.get("id") for r in results}:
            problems.append("tool_result id does not match any tool_call id")
        if "done" not in kinds:
            problems.append("stream ended without a 'done' event")
        if problems:
            last_attempt["problems"] = problems
            continue

        duration = (time.perf_counter() - start) * 1000
        return EvalResult(
            name="chat_stream_emits_tool_events",
            passed=True,
            message=(
                f"{len(calls)} tool call(s) [{', '.join(str(c.get('tool')) for c in calls)}] "
                f"with matching result(s)"
            ),
            duration_ms=duration,
            details={
                "prompt": prompt,
                "tools": [c.get("tool") for c in calls],
                "tool_call_ids": sorted(str(i) for i in call_ids),
                "requires_approval": [c.get("requires_approval") for c in calls],
                "stream_ms": round(elapsed, 1),
                "event_counts": {k: kinds.count(k) for k in set(kinds)},
            },
        )

    duration = (time.perf_counter() - start) * 1000
    return EvalResult(
        name="chat_stream_emits_tool_events",
        passed=False,
        message=f"No prompt produced paired tool_call/tool_result frames: {last_attempt}",
        duration_ms=duration,
        details=last_attempt,
    )


async def eval_chat_stream_first_token_latency(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """Measure time-to-first-token on ``/api/chat/stream``.

    Guards the property Phase 17 was built for: the user sees text appear
    progressively. A regression that buffers the whole reply (or stalls before
    the first delta) shows up as a latency spike / timeout here.
    """
    prompt = "用一句话介绍一下你自己。"
    start = time.perf_counter()
    try:
        events, elapsed = await _stream_chat(client, base, prompt)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            name="chat_stream_first_token_latency",
            passed=False,
            message=f"Stream request failed: {type(exc).__name__}: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    deltas: list[str] = []
    first_token_ms: float | None = None
    kinds: list[str] = []
    for name, payload in events:
        kinds.append(name)
        if name == "text" and isinstance(payload.get("content"), str):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - start) * 1000
            deltas.append(payload["content"])

    total_chars = sum(len(d) for d in deltas)
    duration = (time.perf_counter() - start) * 1000
    within_budget = first_token_ms is not None and first_token_ms <= FIRST_TOKEN_BUDGET_MS
    passed = within_budget and "done" in kinds

    if first_token_ms is None:
        message = f"No text delta received in {elapsed:.0f}ms"
    elif not within_budget:
        message = f"First token in {first_token_ms:.0f}ms exceeds {FIRST_TOKEN_BUDGET_MS:.0f}ms budget"
    else:
        message = f"First token in {first_token_ms:.0f}ms, {len(deltas)} delta(s), {total_chars} chars"

    return EvalResult(
        name="chat_stream_first_token_latency",
        passed=passed,
        message=message,
        duration_ms=duration,
        details={
            "first_token_ms": round(first_token_ms, 1) if first_token_ms is not None else None,
            "budget_ms": FIRST_TOKEN_BUDGET_MS,
            "delta_count": len(deltas),
            "chars": total_chars,
            "stream_ms": round(elapsed, 1),
            "event_counts": {k: kinds.count(k) for k in set(kinds)},
        },
    )


# ---------------------------------------------------------------------------
# Behavioural guards — 断言"行为正确"，而不只是"接口活着"
# ---------------------------------------------------------------------------


#: 网页类提问。Phase 17.10 之前会连触发 browser_open → browser_extract →
#: browser_screenshot（约 40s）。这里卡住"只允许 1 个工具"，防回归。
WEB_PAGE_PROMPT = "帮我打开 https://example.com 看看上面写了什么"

#: 允许出现的工具集合：打开即抽取，不该再补 extract/screenshot。
WEB_PAGE_ALLOWED_TOOLS = {"browser_open", "web_fetch", "web_search"}


async def eval_chat_web_page_uses_one_tool(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """一个网页类提问只能触发 1 个工具（Phase 17.10 收敛的回归护栏）。"""
    start = time.perf_counter()
    try:
        events, elapsed = await _stream_chat(client, base, WEB_PAGE_PROMPT)
    except Exception as exc:  # noqa: BLE001 - runner records the failure
        return EvalResult(
            name="chat_web_page_uses_one_tool",
            passed=False,
            message=f"stream request failed: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    calls = [
        (payload.get("tool_call") or {}).get("tool")
        for name, payload in events
        if name == "tool_call"
    ]
    calls = [c for c in calls if c]
    unknown = [c for c in calls if c not in WEB_PAGE_ALLOWED_TOOLS]

    # 模型有时直接回答、不用工具，这不算失败——只在它真调工具时才校验收敛
    if not calls:
        return EvalResult(
            name="chat_web_page_uses_one_tool",
            passed=True,
            message="no tool call (model answered directly) — convergence not exercised",
            duration_ms=(time.perf_counter() - start) * 1000,
            details={"tool_calls": [], "stream_ms": round(elapsed, 1)},
        )

    passed = len(calls) == 1 and not unknown
    return EvalResult(
        name="chat_web_page_uses_one_tool",
        passed=passed,
        message=(
            f"{len(calls)} tool call(s) {calls}"
            + (f"; unexpected {unknown}" if unknown else "")
        ),
        duration_ms=(time.perf_counter() - start) * 1000,
        details={
            "tool_calls": calls,
            "unexpected": unknown,
            "stream_ms": round(elapsed, 1),
        },
    )


#: 明确要求截图时必须真的调用截图工具——上面那条护栏不能把正常需求也压掉。
SCREENSHOT_PROMPT = "帮我看看 https://example.com 这个页面，顺便截个图"


async def eval_chat_explicit_screenshot_still_fires(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """用户明确要求截图时，browser_screenshot 必须被调用（护栏的反向验证）。"""
    start = time.perf_counter()
    try:
        events, elapsed = await _stream_chat(client, base, SCREENSHOT_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            name="chat_explicit_screenshot_still_fires",
            passed=False,
            message=f"stream request failed: {exc}",
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    calls = [
        (payload.get("tool_call") or {}).get("tool")
        for name, payload in events
        if name == "tool_call"
    ]
    calls = [c for c in calls if c]
    fired = "browser_screenshot" in calls

    return EvalResult(
        name="chat_explicit_screenshot_still_fires",
        passed=fired,
        message=f"tool calls: {calls}",
        duration_ms=(time.perf_counter() - start) * 1000,
        details={"tool_calls": calls, "stream_ms": round(elapsed, 1)},
    )


async def eval_conversation_roundtrip(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """会话生命周期：创建 → 改名 → 分叉 → 读回 → 删除，断言**内容**而非状态码。

    注意：消息没有独立的写入接口，是通过 `/api/chat` 带 `conversation_id`
    持久化的，所以这里覆盖的是会话元数据这条真实路径。
    """
    start = time.perf_counter()
    marker = f"eval-{uuid.uuid4().hex[:8]}"
    notes: list[str] = []
    cleanup_ids: list[str] = []

    async def done(ok: bool) -> EvalResult:
        for cid in reversed(cleanup_ids):  # 先删子会话，避免外键约束
            try:
                await client.delete(f"{base}/api/conversations/{cid}", timeout=10)
            except Exception:  # noqa: BLE001 - 清理失败不影响结论
                pass
        return EvalResult(
            name="conversation_roundtrip",
            passed=ok and not notes,
            message="; ".join(notes) if notes else f"lifecycle OK (marker={marker})",
            duration_ms=(time.perf_counter() - start) * 1000,
            details={"marker": marker, "conversation_ids": cleanup_ids},
        )

    # 1) 创建
    r = await client.post(f"{base}/api/conversations", json={"title": marker}, timeout=10)
    if r.status_code not in (200, 201):
        notes.append(f"create HTTP {r.status_code}")
        return await done(False)
    conv_id = r.json().get("id")
    if not conv_id:
        notes.append(f"create response has no id: {r.text[:120]}")
        return await done(False)
    cleanup_ids.append(conv_id)

    # 2) 读回：标题必须是刚写入的那个
    read = await client.get(f"{base}/api/conversations/{conv_id}", timeout=10)
    if read.status_code != 200:
        notes.append(f"read HTTP {read.status_code}")
        return await done(False)
    if marker not in read.text:
        notes.append("created title not echoed back")
        return await done(False)

    # 3) 改名并确认生效
    renamed = f"{marker}-renamed"
    patch = await client.patch(
        f"{base}/api/conversations/{conv_id}", json={"title": renamed}, timeout=10
    )
    if patch.status_code != 200:
        notes.append(f"patch HTTP {patch.status_code}")
    elif renamed not in patch.text:
        notes.append("rename not applied")

    # 4) 发一条真实消息 —— 消息只有通过 /api/chat 才会被持久化
    chat = await client.post(
        f"{base}/api/chat",
        json={
            "messages": [{"role": "user", "content": marker}],
            "stream": False,
            "conversation_id": conv_id,
        },
        timeout=120,
    )
    if chat.status_code != 200:
        notes.append(f"chat HTTP {chat.status_code}")
        return await done(not notes)

    # 5) 读回：消息必须真的落库了
    read2 = await client.get(f"{base}/api/conversations/{conv_id}", timeout=10)
    if read2.status_code != 200:
        notes.append(f"read-after-chat HTTP {read2.status_code}")
        return await done(not notes)
    messages = read2.json().get("messages") or []
    if not any(marker == (m.get("content") or "").strip() for m in messages):
        notes.append(f"marker not persisted (got {len(messages)} message(s))")
        return await done(not notes)

    # 6) 从第一条消息分叉：子会话必须记住 parent_id
    first_id = messages[0].get("id")
    fork = await client.post(
        f"{base}/api/conversations/{conv_id}/fork",
        json={"message_id": first_id},
        timeout=10,
    )
    if fork.status_code not in (200, 201):
        notes.append(f"fork HTTP {fork.status_code}")
    else:
        child = fork.json()
        if child.get("id"):
            cleanup_ids.append(child["id"])
        if child.get("parent_id") != conv_id:
            notes.append(f"fork parent_id mismatch: {child.get('parent_id')!r}")

    return await done(not notes)


async def eval_model_validate_flags_bad_name(
    client: httpx.AsyncClient, base: str
) -> EvalResult:
    """模型名校验接口要能识别营销名并给出可用建议（Phase 17.11 护栏）。"""
    start = time.perf_counter()
    r = await client.post(
        f"{base}/api/config/models/validate",
        json={"profile_id": "deepseek-v4-flash", "model": "DeepSeek-V4.1-Flash"},
        timeout=30,
    )
    duration = (time.perf_counter() - start) * 1000

    if r.status_code != 200:
        return EvalResult(
            name="model_validate_flags_bad_name",
            passed=False,
            message=f"HTTP {r.status_code}: {r.text[:150]}",
            duration_ms=duration,
        )

    data = r.json()
    valid = data.get("valid")
    suggested = data.get("suggested") or ""
    available = data.get("available") or []

    # 网关只认 deepseek-flash；校验要么判定无效并给出建议，要么判定可用
    # （说明规范化已经把它映射对了）。两种都算通过，但不能"有效却没建议"地蒙混。
    ok = (valid is False and "deepseek-flash" in suggested) or (
        valid is True and "deepseek-flash" in str(data.get("normalized", suggested))
    )
    return EvalResult(
        name="model_validate_flags_bad_name",
        passed=bool(ok),
        message=f"valid={valid} suggested={suggested!r}",
        duration_ms=duration,
        details={
            "valid": valid,
            "suggested": suggested,
            "normalized": data.get("normalized"),
            "available_count": len(available),
        },
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
    eval_chat_stream_emits_tool_events,
    eval_chat_stream_first_token_latency,
    eval_chat_web_page_uses_one_tool,
    eval_chat_explicit_screenshot_still_fires,
    eval_conversation_roundtrip,
    eval_model_validate_flags_bad_name,
]
