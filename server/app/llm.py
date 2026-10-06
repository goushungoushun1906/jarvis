"""JARVIS LLM client — unified async wrapper around the OpenAI-compatible API.

Two entry points:

* :func:`call_llm` — non-streaming, returns the full message dict including
  native ``tool_calls``. Used by the agent loop and vision tools.
* :func:`call_llm_stream` — **real** streaming generator (Phase 17.1). Yields
  text deltas as they arrive *and* reassembles streamed ``delta.tool_calls``
  fragments into complete tool calls, so the agent loop can push tool events
  to the UI in real time.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .config import settings
from .model_router import (
    profile_to_api_credentials,
    resolve_profile_chain,
    select_tier,
)

logger = logging.getLogger("jarvis")


def _is_model_unavailable(error: LLMError) -> bool:
    """Return True if the error indicates the selected model is not reachable.

    Includes network/connection errors so the router can fall back to the next
    configured profile instead of failing the whole request. ``429`` counts as
    unavailable: a rate-limited channel is saturated, and a sibling profile is
    a better bet than surfacing the failure to the user.
    """
    if error.status_code in (429, 502, 503, 504):
        return True
    detail = (error.detail or "").lower()
    return any(
        k in detail
        for k in (
            "model_not_found",
            "no available channel",
            "invalid model",
            "rate limit",
            "too many requests",
            "connection",
            "connect",
            "refused",
            "unreachable",
            "timeout",
            "name or service not known",
            "getaddrinfo failed",
        )
    )


async def call_llm(
    messages: list[dict],
    stream: bool = False,
    tools: list[dict] | None = None,
    explicit_tier: str | None = None,
) -> AsyncGenerator[str, None] | dict:
    """Call the LLM API (non-streaming).

    Args:
        messages:       List of ``{"role": ..., "content": ...}`` dicts.
        stream:         Legacy flag. When *True* a text-only generator is
                        returned that delegates to :func:`call_llm_stream`.
                        Tool-call chunks are dropped by that shim — callers
                        that need tool calls must use :func:`call_llm_stream`.
        tools:          Optional list of tool schemas in OpenAI function-calling format.
        explicit_tier:  Optional "fast"/"mid"/"deep" tier override.

    Returns:
        A dict ``{content, tool_calls, role, tier, model}``.
    """
    if stream:
        return _text_only_stream(messages, tools=tools, explicit_tier=explicit_tier)

    # ------------------------------------------------------------------
    # Model routing: choose a profile for this call
    # ------------------------------------------------------------------
    tier = select_tier(messages, tools=tools, explicit_tier=explicit_tier)
    profile_chain = await resolve_profile_chain(tier)
    if not profile_chain:
        raise RuntimeError("No model profile is configured")

    input_chars = _estimate_chars(messages)
    start_time = time.perf_counter()

    # Try profiles in fallback order
    last_error: LLMError | None = None
    for idx, profile in enumerate(profile_chain):
        base_url, api_key, model_id = profile_to_api_credentials(profile)
        profile_id = profile.get("id")
        logger.info(
            "LLM call attempt=%d tier=%s profile=%s model=%s base_url=%s",
            idx + 1, tier, profile_id, model_id, base_url,
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": model_id,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            return await _non_stream_response(
                base_url,
                headers,
                payload,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                start_time=start_time,
            )
        except LLMError as e:
            last_error = e
            if _is_model_unavailable(e) and idx < len(profile_chain) - 1:
                logger.warning(
                    "Model unavailable (profile=%s model=%s): %s. Trying fallback profile...",
                    profile_id, model_id, e.message,
                )
                continue
            # Record failed call and re-raise
            await _record_call(
                model=model_id,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=0,
                duration_ms=_elapsed_ms(start_time),
                success=False,
                error=str(e)[:500],
            )
            raise
        except Exception as e:
            # Record failed call and re-raise
            await _record_call(
                model=model_id,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=0,
                duration_ms=_elapsed_ms(start_time),
                success=False,
                error=str(e)[:500],
            )
            raise

    # All profiles exhausted (should only reach here if every profile raised a model-unavailable error)
    if last_error:
        await _record_call(
            model=profile_chain[-1].get("model", "unknown"),
            tier=tier,
            profile_id=profile_chain[-1].get("id"),
            input_chars=input_chars,
            output_chars=0,
            duration_ms=_elapsed_ms(start_time),
            success=False,
            error=str(last_error)[:500],
        )
        raise last_error
    raise RuntimeError("No model profile succeeded")


async def _non_stream_response(
    base_url: str,
    headers: dict,
    payload: dict,
    tier: str,
    profile_id: str | None,
    input_chars: int,
    start_time: float,
) -> dict:
    """Return the full LLM message dict: {content, tool_calls, role, tier, model}."""
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.debug("LLM non-stream response: %s", json.dumps(data, ensure_ascii=False)[:1000])
            msg = data["choices"][0]["message"]
            content = msg.get("content")
            if not content:
                logger.warning("LLM returned empty content for model=%s", payload["model"])
            output_chars = len(content) if isinstance(content, str) else 0

            await _record_call(
                model=payload["model"],
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=output_chars,
                duration_ms=_elapsed_ms(start_time),
                success=True,
            )

            return {
                "content": content,
                "tool_calls": msg.get("tool_calls"),
                "role": msg.get("role", "assistant"),
                "tier": tier,
                "model": payload["model"],
            }
        except httpx.TimeoutException:
            logger.error("LLM request timed out")
            raise LLMError("LLM request timed out", status_code=504)
        except httpx.RequestError as e:
            # Covers connection refused, DNS failures, network unreachable, etc.
            logger.error("LLM API request failed: %s", e)
            raise LLMError(
                "LLM API request failed",
                detail=str(e),
                status_code=502,
            )
        except httpx.HTTPStatusError as e:
            logger.error("LLM HTTP error: %s - %s", e.response.status_code, e.response.text)
            raise LLMError(
                f"LLM API error: {e.response.status_code}",
                detail=e.response.text[:500],
                status_code=e.response.status_code,
            )
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON: %s", e)
            raise LLMError(
                "LLM returned an invalid response",
                detail=str(e),
                status_code=502,
            )
        except (KeyError, IndexError) as e:
            logger.error("LLM unexpected response format: %s", e)
            raise LLMError("Unexpected LLM response format", detail=str(e))
        except Exception as e:
            logger.exception("LLM unexpected error")
            raise LLMError("Unexpected LLM error", detail=repr(e))


# ==================================================================
# Phase 17.1 — real streaming with tool-call reassembly
# ==================================================================


@dataclass
class LLMChunk:
    """One increment produced by :func:`call_llm_stream`.

    ``kind`` is one of:

    * ``"text"``       — ``delta`` carries a text fragment from the model.
    * ``"tool_calls"`` — ``tool_calls`` carries the **complete** reassembled
      tool call list. ``error`` is set (with an empty list) when the provider
      claimed tool calls but the fragments could not be recovered.
    * ``"finish"``     — ``finish_reason`` carries the provider's stop reason
      (``"stop"`` / ``"tool_calls"`` / ``"length"`` / ``None``).
    """

    kind: Literal["text", "tool_calls", "finish"]
    delta: str = ""
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None
    error: str | None = None


def _merge_name_fragment(current: str, fragment: str) -> str:
    """Merge a streamed ``function.name`` fragment.

    The OpenAI spec sends the name once, but compatible gateways vary: some
    repeat it on every chunk, a few stream it in pieces. Concatenating blindly
    would produce ``"web_searchweb_search"``, so repeats are detected and
    dropped before appending.
    """
    if not fragment:
        return current
    if not current:
        return fragment
    if fragment == current or current.endswith(fragment):
        return current
    return current + fragment


class ToolCallAccumulator:
    """Reassemble complete tool calls from streamed OpenAI ``delta.tool_calls``.

    OpenAI streams tool calls as fragments keyed by ``index``::

        delta.tool_calls = [{"index": 0, "id": "call_x", "type": "function",
                             "function": {"name": "web_search",
                                          "arguments": "{\\"qu"}}]

    ``function.arguments`` arrives as an *incremental string* and must be
    concatenated per index before it can be parsed as JSON. A non-conforming
    gateway may instead send a complete JSON object — both shapes are handled.
    """

    def __init__(self) -> None:
        self._slots: dict[int, dict] = {}

    def feed(self, deltas: Any) -> None:
        """Absorb one ``delta.tool_calls`` array."""
        if not isinstance(deltas, list):
            return
        for raw in deltas:
            if not isinstance(raw, dict):
                continue

            index = raw.get("index")
            if not isinstance(index, int):
                # Some gateways omit ``index`` entirely; assume the open call.
                index = max(self._slots) if self._slots else 0

            fn = raw.get("function")
            if fn is not None and not isinstance(fn, dict):
                # Malformed entry — do not create a slot for it, otherwise a
                # stray value would produce a phantom "empty function name" call.
                logger.debug("Ignoring malformed tool_call entry: %.200s", raw)
                continue

            slot = self._slots.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )

            if raw.get("id"):
                slot["id"] = str(raw["id"])
            if raw.get("type"):
                slot["type"] = str(raw["type"])

            if not fn:
                continue
            if fn.get("name"):
                slot["function"]["name"] = _merge_name_fragment(
                    slot["function"]["name"], str(fn["name"])
                )
            args = fn.get("arguments")
            if isinstance(args, str):
                slot["function"]["arguments"] += args
            elif isinstance(args, dict):
                # Non-standard: the gateway sent the finished object at once.
                slot["function"]["arguments"] = json.dumps(args, ensure_ascii=False)

    @property
    def empty(self) -> bool:
        return not self._slots

    def build(self) -> tuple[list[dict], str | None]:
        """Return ``(tool_calls, error)``.

        Well-formed calls are returned in OpenAI function-calling format with
        ``arguments`` as a JSON *string* (matching what :func:`call_llm`
        returns). ``error`` is set when a call was seen but unusable — the
        caller uses that signal to attempt non-streaming recovery.
        """
        calls: list[dict] = []
        errors: list[str] = []

        for position, index in enumerate(sorted(self._slots)):
            slot = self._slots[index]
            name = slot["function"]["name"].strip()
            raw_args = slot["function"]["arguments"].strip()

            if not name:
                errors.append(f"call #{index}: empty function name")
                continue

            if raw_args:
                try:
                    parsed = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    errors.append(f"call #{index} ({name}): invalid arguments JSON: {exc}")
                    continue
                if not isinstance(parsed, dict):
                    errors.append(f"call #{index} ({name}): arguments is not an object")
                    continue
                arguments = json.dumps(parsed, ensure_ascii=False)
            else:
                arguments = "{}"

            calls.append(
                {
                    "id": slot["id"] or f"call_{position}",
                    "type": slot["type"] or "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )

        return calls, ("; ".join(errors) if errors else None)


async def _iter_openai_sse(
    base_url: str,
    headers: dict,
    payload: dict,
    timeout: float,
) -> AsyncGenerator[dict, None]:
    """Yield parsed SSE objects from an OpenAI-compatible stream.

    Transport and protocol failures are normalised into :class:`LLMError` so
    the caller can apply the fallback policy.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    # ``raise_for_status()`` cannot read the body of a streamed
                    # response, which would silently discard the gateway's error
                    # message — the single most useful thing when debugging a
                    # provider. Read it explicitly instead.
                    body = ""
                    try:
                        raw = await resp.aread()
                        body = raw.decode("utf-8", errors="replace")[:500]
                    except Exception:
                        pass
                    logger.error("LLM stream HTTP error: %s - %s", resp.status_code, body)
                    raise LLMError(
                        f"LLM stream API error: {resp.status_code}",
                        detail=body,
                        status_code=resp.status_code,
                    )

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("Skipping unparsable SSE payload: %.200s", data_str)
                        continue
        except LLMError:
            raise
        except httpx.TimeoutException:
            logger.error("LLM stream timed out")
            raise LLMError("LLM stream timed out", status_code=504)
        except httpx.HTTPStatusError as e:
            logger.error("LLM stream HTTP error: %s", e.response.status_code)
            raise LLMError(
                f"LLM stream API error: {e.response.status_code}",
                detail="",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error("LLM stream API request failed: %s", e)
            raise LLMError(
                "LLM API request failed",
                detail=str(e),
                status_code=502,
            )
        except Exception as e:
            logger.exception("LLM stream unexpected error")
            raise LLMError("LLM stream unexpected error", detail=repr(e))


async def _fetch_tool_calls_non_stream(
    base_url: str,
    headers: dict,
    payload: dict,
) -> list[dict]:
    """Last-resort recovery: re-ask the same model with ``stream=False``.

    Only used when the streamed tool-call fragments were unusable. Costs one
    extra request, so it is logged at warning level for cost visibility.
    """
    retry_payload = dict(payload)
    retry_payload["stream"] = False

    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=retry_payload,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_calls = data["choices"][0]["message"].get("tool_calls") or []
        except Exception as exc:
            logger.warning("Non-streaming tool-call recovery failed: %s", exc)
            return []

    normalised: list[dict] = []
    for position, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        args = fn.get("arguments")
        if isinstance(args, dict):
            arguments = json.dumps(args, ensure_ascii=False)
        else:
            arguments = str(args or "{}")
        normalised.append(
            {
                "id": str(call.get("id") or f"call_{position}"),
                "type": str(call.get("type") or "function"),
                "function": {"name": name, "arguments": arguments},
            }
        )
    return normalised


async def call_llm_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    explicit_tier: str | None = None,
) -> AsyncGenerator[LLMChunk, None]:
    """Stream a completion, preserving tool calls.

    Unlike :func:`call_llm`, this is a genuine streaming generator: text deltas
    are yielded the moment the provider sends them, so the caller can push
    first-token latency down to the model's own time-to-first-token.

    Fallback policy (Phase 17.1)
    ----------------------------
    * A profile may only be swapped while **nothing has been emitted yet**.
      Once text has reached the caller, switching models would duplicate or
      corrupt the visible message, so the error is surfaced instead.
    * If the provider signalled ``tool_calls`` but the reassembled arguments
      are unusable, one non-streaming request is made to recover them — but
      only when no text was emitted yet (otherwise the retry would duplicate
      the text that was already shown).
    * A stream that ends with no text, no tool calls and no ``finish_reason``
      is treated as a silent provider failure and falls through to the next
      profile.

    Yields:
        :class:`LLMChunk` objects in arrival order.
    """
    tier = select_tier(messages, tools=tools, explicit_tier=explicit_tier)
    profile_chain = await resolve_profile_chain(tier)
    if not profile_chain:
        raise RuntimeError("No model profile is configured")

    input_chars = _estimate_chars(messages)
    start_time = time.perf_counter()
    last_error: LLMError | None = None

    for idx, profile in enumerate(profile_chain):
        base_url, api_key, model_id = profile_to_api_credentials(profile)
        profile_id = profile.get("id")
        logger.info(
            "LLM stream attempt=%d tier=%s profile=%s model=%s base_url=%s",
            idx + 1, tier, profile_id, model_id, base_url,
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        accumulator = ToolCallAccumulator()
        emitted_any = False          # text has already reached the caller
        output_chars = 0
        finish_reason: str | None = None

        try:
            async for event in _iter_openai_sse(
                base_url, headers, payload, settings.llm_stream_timeout
            ):
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}

                delta = choice.get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    emitted_any = True
                    output_chars += len(text)
                    yield LLMChunk(kind="text", delta=text)

                if delta.get("tool_calls"):
                    accumulator.feed(delta["tool_calls"])

                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])

        except LLMError as exc:
            last_error = exc
            if not emitted_any and _is_model_unavailable(exc) and idx < len(profile_chain) - 1:
                logger.warning(
                    "Stream model unavailable before first chunk (profile=%s model=%s): %s. "
                    "Trying fallback profile...",
                    profile_id, model_id, exc.message,
                )
                continue
            await _record_call(
                model=model_id,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=output_chars,
                duration_ms=_elapsed_ms(start_time),
                success=False,
                error=str(exc)[:500],
            )
            raise
        except Exception as exc:
            await _record_call(
                model=model_id,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=output_chars,
                duration_ms=_elapsed_ms(start_time),
                success=False,
                error=str(exc)[:500],
            )
            raise

        # --- stream finished cleanly ------------------------------------
        tool_calls, parse_error = accumulator.build()

        # Recovery: fragments unusable AND nothing shown to the user yet.
        if parse_error and not tool_calls and not emitted_any:
            logger.warning(
                "Streamed tool calls unusable (profile=%s): %s. Retrying non-streaming...",
                profile_id, parse_error,
            )
            recovered = await _fetch_tool_calls_non_stream(base_url, headers, payload)
            if recovered:
                tool_calls, parse_error = recovered, None

        # Silent failure: the provider closed without producing anything.
        if not emitted_any and not tool_calls and parse_error is None and finish_reason is None:
            if idx < len(profile_chain) - 1:
                logger.warning(
                    "Stream produced no content (profile=%s model=%s); trying fallback profile",
                    profile_id, model_id,
                )
                continue
            await _record_call(
                model=model_id,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=0,
                duration_ms=_elapsed_ms(start_time),
                success=False,
                error="empty stream",
            )
            raise LLMError("LLM stream produced no content", status_code=502)

        if tool_calls:
            # Tool arguments are model output too, so they count towards cost.
            output_chars += sum(len(c["function"]["arguments"]) for c in tool_calls)
            yield LLMChunk(kind="tool_calls", tool_calls=tool_calls)

        if parse_error:
            # Surfaced explicitly rather than swallowed: the agent loop needs
            # to know a tool call the model wanted was lost.
            logger.error("Tool calls could not be recovered: %s", parse_error)
            yield LLMChunk(kind="tool_calls", tool_calls=[], error=parse_error)

        await _record_call(
            model=model_id,
            tier=tier,
            profile_id=profile_id,
            input_chars=input_chars,
            output_chars=output_chars,
            duration_ms=_elapsed_ms(start_time),
            success=True,
        )
        yield LLMChunk(kind="finish", finish_reason=finish_reason)
        return

    if last_error:
        raise last_error
    raise RuntimeError("No model profile succeeded")


def _text_only_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    explicit_tier: str | None = None,
) -> AsyncGenerator[str, None]:
    """Backward-compatible text-only view over :func:`call_llm_stream`.

    Preserves the historical ``AsyncGenerator[str, None]`` contract of
    ``call_llm(stream=True)``. Tool-call chunks are dropped — callers that
    need them must consume :func:`call_llm_stream` directly.
    """

    async def _gen() -> AsyncGenerator[str, None]:
        async for chunk in call_llm_stream(
            messages, tools=tools, explicit_tier=explicit_tier
        ):
            if chunk.kind == "text" and chunk.delta:
                yield chunk.delta

    return _gen()



def _estimate_chars(messages: list[dict]) -> int:
    """Estimate input character count for cost tracking."""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += len(str(part.get("text", "")))
                    elif part.get("type") == "image_url":
                        total += 1000  # rough image token proxy
    return total


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


async def _record_call(**kwargs) -> None:
    """Persist one LLM invocation to the database, ignoring errors."""
    try:
        from .database import record_llm_call
        await record_llm_call(**kwargs)
    except Exception as e:
        logger.warning("Failed to record LLM call metrics: %s", e)


# ------------------------------------------------------------------
# Custom exception so callers can catch LLM-specific errors
# ------------------------------------------------------------------

class LLMError(Exception):
    def __init__(
        self,
        message: str,
        detail: str = "",
        status_code: int = 500,
    ):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code
