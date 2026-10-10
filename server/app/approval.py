"""Human-in-the-loop approval gate for tool execution (A6).

Why this exists
---------------
``BaseTool.requires_approval`` was advisory metadata: it travelled to the
frontend with every ``tool_call`` event, the frontend rendered a dialog, and
the user's answer was POSTed to ``/api/tools/approve`` — an endpoint that did
not exist. The 404 was swallowed silently, so every call the user ever
"rejected" ran anyway.

This module makes approval *actually gate execution*. The shape:

* One :class:`ApprovalGate` per chat stream. The agent loop registers a
  pending approval before announcing the tool call, then awaits the decision
  **before** touching the tool.
* A process-wide :class:`ApprovalBroker` maps ``call_id -> gate`` so the HTTP
  endpoint (a different request, different task) can hand the decision back
  into the still-open stream.
* Nothing executes on silence: an unanswered request times out and is denied.

Fail-closed is deliberate. A false positive costs the user one extra click; a
false negative lets the model run ``workspace_run_command`` on its own
judgement, which is the exact thing this is meant to prevent.

Two escape hatches, because guessing wrong here locks the user out of their
own assistant:

* ``JARVIS_APPROVAL_TIMEOUT`` — how long to wait before auto-denying
  (seconds, default 300).
* ``ApprovalGate(auto_deny=True)`` — denies everything immediately, for
  callers that cannot show a dialog (the non-streaming ``/api/chat``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("jarvis")

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("JARVIS_APPROVAL_TIMEOUT", "300"))


@dataclass
class PendingApproval:
    """One tool call waiting on a human decision."""

    call_id: str
    tool: str
    args: dict = field(default_factory=dict)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None
    created_at: float = field(default_factory=time.monotonic)


class ApprovalGate:
    """Per-stream gate between "model asked for a tool" and "tool runs"."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        broker: "ApprovalBroker | None" = None,
        auto_deny: bool = False,
    ) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._timeout = timeout
        self._broker = broker
        self._auto_deny = auto_deny
        self._closed = False
        # Why the most recent wait() said no. Read straight after wait()
        # returns False; gated tools run sequentially so there is no race.
        self.last_reason: str = "rejected"

    @property
    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def register(self, call_id: str, tool: str, args: dict | None = None) -> PendingApproval:
        """Open a pending approval. Called *before* the tool_call is announced."""
        pending = PendingApproval(call_id=call_id, tool=tool, args=args or {})
        self._pending[call_id] = pending
        if self._broker is not None:
            self._broker.bind(call_id, self)
        logger.info("Approval requested: %s (call_id=%s)", tool, call_id)
        return pending

    def resolve(self, call_id: str, approved: bool) -> bool:
        """Record a decision. Returns False if nothing was pending."""
        pending = self._pending.get(call_id)
        if pending is None:
            return False
        pending.approved = bool(approved)
        pending.event.set()
        return True

    async def wait(self, call_id: str) -> bool:
        """Await the decision for ``call_id``.

        Returns True only on an explicit approval. Times out, an unregistered
        call_id, and auto-deny mode all return False — the caller must treat
        False as "do not execute".
        """
        if self._auto_deny:
            self.last_reason = "unsupported"
            logger.info("Auto-deny gate: refusing %s (call_id=%s)", call_id, call_id)
            self._discard(call_id)
            return False

        pending = self._pending.get(call_id)
        if pending is None:
            # Not registered -> we cannot ask anyone -> do not execute.
            self.last_reason = "unregistered"
            logger.warning(
                "Tool call %s awaited approval without being registered; denying.",
                call_id,
            )
            return False

        reason = "rejected"
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            pending.approved = False
            reason = "timeout"
            logger.warning(
                "Approval for %s (call_id=%s) timed out after %.0fs; denying.",
                pending.tool,
                call_id,
                self._timeout,
            )
        except asyncio.CancelledError:
            # Stream torn down (client disconnect). Unbind, then re-raise so
            # the caller's cancellation semantics are preserved.
            self._discard(call_id)
            raise
        finally:
            self._discard(call_id)

        self.last_reason = "approved" if pending.approved else reason
        return bool(pending.approved)

    def _discard(self, call_id: str) -> None:
        self._pending.pop(call_id, None)
        if self._broker is not None:
            self._broker.unbind(call_id, self)

    def close(self) -> None:
        """Deny everything still pending (stream ended, disconnect, error)."""
        self._closed = True
        for call_id in list(self._pending):
            pending = self._pending[call_id]
            if pending.approved is None:
                pending.approved = False
            pending.event.set()
            self._discard(call_id)

    @property
    def closed(self) -> bool:
        return self._closed


class ApprovalBroker:
    """Process-wide registry letting an HTTP request reach a live stream."""

    def __init__(self) -> None:
        self._gates: dict[str, ApprovalGate] = {}

    def bind(self, call_id: str, gate: ApprovalGate) -> None:
        previous = self._gates.get(call_id)
        if previous is not None and previous is not gate:
            # Fallback ids (``call_0``) are not unique across conversations.
            # Last writer wins; the loser simply times out and denies.
            logger.warning(
                "Approval call_id collision on %r — an earlier waiter will time out.",
                call_id,
            )
        self._gates[call_id] = gate

    def unbind(self, call_id: str, gate: ApprovalGate) -> None:
        if self._gates.get(call_id) is gate:
            del self._gates[call_id]

    def resolve(self, call_id: str, approved: bool) -> bool:
        gate = self._gates.get(call_id)
        if gate is None:
            return False
        return gate.resolve(call_id, approved)

    def pending_count(self) -> int:
        return len(self._gates)


# Singleton: the /api/tools/approve route and the chat stream share a loop.
broker = ApprovalBroker()


def new_gate(auto_deny: bool = False) -> ApprovalGate:
    """Create a stream-scoped gate bound to the global broker."""
    return ApprovalGate(broker=broker, auto_deny=auto_deny)


def denied_result(tool_name: str, reason: str = "rejected") -> Any:
    """Build the ToolResult handed back to the model when a call is denied."""
    from .tools import ToolResult  # local import avoids a cycle at import time

    if reason == "timeout":
        detail = "用户未在限定时间内确认，已自动取消该操作"
    elif reason == "unsupported":
        detail = "当前调用方式不支持人工确认，已拒绝执行"
    elif reason == "unregistered":
        detail = "该调用未登记审批，出于安全考虑已拒绝执行"
    else:
        detail = "用户拒绝执行该操作"

    return ToolResult(
        f"{detail}（工具：{tool_name}）。"
        f"请不要再次尝试自动执行，向用户说明情况并询问替代方案。",
        success=False,
        metadata={"denied": True, "reason": reason},
    )
