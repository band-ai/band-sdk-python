"""Event-driven wait primitives for live E2E tests.

Deterministic, not timing-based. The room processes messages in FIFO order
(a single per-room process loop drains one queue), so completion is detected
from positive signals — an agent reply, or the echo of a unique probe token —
never from a silence window. ``deadline_s`` is a *failure* deadline: it bounds
how long we wait before declaring the agent stuck, and is never used as a
success signal.

Everything is built on one ``ReplyCapture.wait_until`` mechanism, with named
helpers for the common predicates so tests avoid raw lambdas:

- ``ReplyCapture.wait_for_sender`` — turn-boundary capture.
- ``drain`` — token-barrier: send a unique-nonce probe and wait for its echo;
  FIFO ordering proves every message before the probe was processed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from band.client.streaming import MessageCreatedPayload, WebSocketClient

from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.helpers import TrackingWebSocketClient

logger = logging.getLogger(__name__)

DEFAULT_DEADLINE_S = 60.0


class ReplyCapture:
    """Collects a room's agent text replies and waits on predicates over them.

    Subscribed before any trigger is sent (see ``reply_capture``) so no reply
    can be missed. ``messages`` is the running buffer; ``wait_until`` blocks
    until a predicate over it holds, or raises ``TimeoutError`` at the deadline.

    One waiter at a time: the internal nudge is a single shared ``asyncio.Event``,
    so call ``wait_until`` sequentially on a capture, not from concurrent tasks.
    """

    def __init__(self, room_id: str, *, deadline_s: float = DEFAULT_DEADLINE_S) -> None:
        self.room_id = room_id
        self.deadline_s = deadline_s  # default failure deadline for waits
        self.messages: list[MessageCreatedPayload] = []
        self._nudge = asyncio.Event()

    def _on_message(self, payload: MessageCreatedPayload) -> None:
        if payload.sender_type == "Agent" and payload.message_type == "text":
            self.messages.append(payload)
            logger.info(
                "Captured agent reply in room %s from %s: %s",
                self.room_id,
                payload.sender_name or payload.sender_id,
                payload.content[:80],
            )
            self._nudge.set()

    async def wait_until(
        self,
        predicate: Callable[[list[MessageCreatedPayload]], bool],
        *,
        deadline_s: float | None = None,
    ) -> list[MessageCreatedPayload]:
        """Block until ``predicate(messages)`` holds; raise ``TimeoutError`` at
        the deadline (defaults to ``self.deadline_s``). The deadline is enforced
        by ``wait_for``; the inner loop re-checks the predicate on each nudge."""
        deadline = self.deadline_s if deadline_s is None else deadline_s

        async def await_predicate() -> None:
            while not predicate(self.messages):
                self._nudge.clear()
                await self._nudge.wait()

        try:
            await asyncio.wait_for(await_predicate(), timeout=deadline)
        except TimeoutError:
            raise TimeoutError(
                f"Predicate not satisfied within {deadline:.0f}s in room "
                f"{self.room_id} (captured {len(self.messages)} reply/replies)"
            ) from None
        return list(self.messages)

    async def wait_for_sender(
        self, sender_id: str, *, deadline_s: float | None = None
    ) -> list[MessageCreatedPayload]:
        """Block until an agent reply from ``sender_id`` arrives."""
        return await self.wait_until(
            lambda msgs: any(m.sender_id == sender_id for m in msgs),
            deadline_s=deadline_s,
        )


@asynccontextmanager
async def reply_capture(
    ws: WebSocketClient | TrackingWebSocketClient,
    room_id: str,
    *,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> AsyncIterator[ReplyCapture]:
    """Subscribe to a room before sending, yield a capture, leave on exit."""
    capture = ReplyCapture(room_id, deadline_s=deadline_s)

    async def handler(payload: MessageCreatedPayload) -> None:
        capture._on_message(payload)

    await ws.join_chat_room_channel(room_id, handler)
    try:
        yield capture
    finally:
        await ws.leave_chat_room_channel(room_id)


async def drain(
    capture: ReplyCapture,
    user_ops: UserOps,
    room_id: str,
    *,
    mention_id: str,
    mention_name: str,
    deadline_s: float | None = None,
) -> str:
    """Token-barrier drain: probe the agent and wait for it to echo the nonce.

    Sends ``Respond with exactly: DRAIN-<nonce>`` as the last message. Because
    the room processes messages in order, an agent reply containing the nonce
    proves every earlier message was processed. Returns the nonce. The deadline
    defaults to the capture's (``deadline_s=None``).
    """
    nonce = f"DRAIN-{uuid.uuid4().hex[:8]}"
    await user_ops.send_message(
        room_id,
        f"Respond with exactly: {nonce}",
        mention_id=mention_id,
        mention_name=mention_name,
    )
    await capture.wait_until(
        lambda msgs: any(nonce in m.content for m in msgs), deadline_s=deadline_s
    )
    return nonce
