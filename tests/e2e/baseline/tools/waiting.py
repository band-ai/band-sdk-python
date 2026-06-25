"""Event-driven wait primitives for live E2E tests.

Deterministic, not timing-based. The room processes messages in FIFO order
(a single per-room process loop drains one queue), so completion is detected
from positive signals — an agent reply, or the echo of a unique probe token —
never from a silence window. ``deadline_s`` is a *failure* deadline: it bounds
how long we wait before declaring the agent stuck, and is never used as a
success signal.

Two primitives over one ``ReplyCapture.wait_until`` mechanism:

- ``wait_for_reply`` — turn-boundary capture for a single request.
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

from tests.e2e.baseline.tools.user_ops import UserOps
from tests.e2e.helpers import TrackingWebSocketClient

logger = logging.getLogger(__name__)

DEFAULT_DEADLINE_S = 60.0


class ReplyCapture:
    """Collects an room's agent text replies and waits on predicates over them.

    Subscribed before any trigger is sent (see ``reply_capture``) so no reply
    can be missed. ``messages`` is the running buffer; ``wait_until`` blocks
    until a predicate over it holds, or raises ``TimeoutError`` at the deadline.
    """

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
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
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> list[MessageCreatedPayload]:
        """Block until ``predicate(messages)`` holds; raise at the deadline."""
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline_s
        while not predicate(self.messages):
            remaining = end - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Predicate not satisfied within {deadline_s:.0f}s in room "
                    f"{self.room_id} (captured {len(self.messages)} reply/replies)"
                )
            self._nudge.clear()
            try:
                await asyncio.wait_for(self._nudge.wait(), timeout=remaining)
            except TimeoutError:
                continue  # loop re-checks predicate and the deadline
        return list(self.messages)


@asynccontextmanager
async def reply_capture(
    ws: WebSocketClient | TrackingWebSocketClient, room_id: str
) -> AsyncIterator[ReplyCapture]:
    """Subscribe to a room before sending, yield a capture, leave on exit."""
    capture = ReplyCapture(room_id)

    async def handler(payload: MessageCreatedPayload) -> None:
        capture._on_message(payload)

    await ws.join_chat_room_channel(room_id, handler)
    try:
        yield capture
    finally:
        await ws.leave_chat_room_channel(room_id)


async def wait_for_reply(
    capture: ReplyCapture,
    *,
    min_messages: int = 1,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> list[MessageCreatedPayload]:
    """Wait until at least ``min_messages`` agent replies have arrived."""
    return await capture.wait_until(
        lambda msgs: len(msgs) >= min_messages, deadline_s=deadline_s
    )


async def drain(
    capture: ReplyCapture,
    user_ops: UserOps,
    room_id: str,
    *,
    mention_id: str,
    mention_name: str,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> str:
    """Token-barrier drain: probe the agent and wait for it to echo the nonce.

    Sends ``Respond with exactly: DRAIN-<nonce>`` as the last message. Because
    the room processes messages in order, an agent reply containing the nonce
    proves every earlier message was processed. Returns the nonce.
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
