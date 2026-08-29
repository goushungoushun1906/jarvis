"""JARVIS LLM client — unified async wrapper around the OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator

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
    configured profile instead of failing the whole request.
    """
    if error.status_code in (502, 503, 504):
        return True
    detail = (error.detail or "").lower()
    return any(
        k in detail
        for k in (
            "model_not_found",
            "no available channel",
            "invalid model",
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
    """Call the LLM API.

    Args:
        messages:       List of ``{"role": ..., "content": ...}`` dicts.
        stream:         If *True*, yield content chunks; otherwise return the full
                        response string.
        tools:          Optional list of tool schemas in OpenAI function-calling format.
        explicit_tier:  Optional "fast"/"mid"/"deep" tier override.

    Returns:
        An async generator (streaming) or a plain string (non-streaming).
    """
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
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            if stream:
                gen = _stream_response(
                    base_url,
                    headers,
                    payload,
                    tier=tier,
                    profile_id=profile_id,
                    input_chars=input_chars,
                    start_time=start_time,
                )
                # Return an async generator that records metrics on completion
                return _wrap_stream_generator(
                    gen,
                    model=model_id,
                    tier=tier,
                    profile_id=profile_id,
                    input_chars=input_chars,
                    start_time=start_time,
                )
            else:
                result = await _non_stream_response(
                    base_url,
                    headers,
                    payload,
                    tier=tier,
                    profile_id=profile_id,
                    input_chars=input_chars,
                    start_time=start_time,
                )
                return result
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


async def _stream_response(
    base_url: str,
    headers: dict,
    payload: dict,
    tier: str,
    profile_id: str | None,
    input_chars: int,
    start_time: float,
):
    """Yield content chunks and final metadata."""
    async with httpx.AsyncClient(timeout=settings.llm_stream_timeout) as client:
        try:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data_str)
                            logger.debug("LLM stream chunk: %s", json.dumps(chunk, ensure_ascii=False)[:500])
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                            elif delta.get("tool_calls"):
                                logger.debug("LLM stream tool_calls chunk (ignored in stream mode)")
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.TimeoutException:
            logger.error("LLM stream timed out")
            raise LLMError("LLM stream timed out", status_code=504)
        except httpx.RequestError as e:
            logger.error("LLM stream API request failed: %s", e)
            raise LLMError(
                "LLM API request failed",
                detail=str(e),
                status_code=502,
            )
        except httpx.HTTPStatusError as e:
            logger.error("LLM stream HTTP error: %s", e.response.status_code)
            raise LLMError(
                f"LLM stream API error: {e.response.status_code}",
                detail=e.response.text[:500],
                status_code=e.response.status_code,
            )
        except Exception as e:
            logger.exception("LLM stream unexpected error")
            raise LLMError("LLM stream unexpected error", detail=repr(e))


def _wrap_stream_generator(
    gen,
    *,
    model: str,
    tier: str,
    profile_id: str | None,
    input_chars: int,
    start_time: float,
):
    """Wrap the raw stream generator so we can record call metrics at the end."""
    output_chars = 0
    success = True
    error_msg: str | None = None

    async def wrapped():
        nonlocal output_chars, success, error_msg
        try:
            async for chunk in gen:
                if isinstance(chunk, str):
                    output_chars += len(chunk)
                    yield chunk
        except Exception as e:
            success = False
            error_msg = str(e)[:500]
            raise
        finally:
            await _record_call(
                model=model,
                tier=tier,
                profile_id=profile_id,
                input_chars=input_chars,
                output_chars=output_chars,
                duration_ms=_elapsed_ms(start_time),
                success=success,
                error=error_msg,
            )

    return wrapped()


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
