"""Event-driven wait primitives for live E2E tests.

Deterministic, not timing-based. Completion is detected from the platform's own
delivery-status signal, never from a silence window. ``deadline_s`` is a
*failure* deadline: it bounds how long we wait before declaring the agent stuck,
and is never used as a success signal.

The surface is small — methods on ``ReplyCapture``:

- ``wait_until`` — the engine: block until a predicate over captured state holds,
  or raise ``TimeoutError`` at the deadline. Use directly for a custom condition.
- ``wait_for_delivery`` — block until a recipient's delivery status for a message
  reaches one of the given ``DeliveryStatus`` values; the general delivery waiter.
- ``wait_for_processed`` — the common barrier built on ``wait_for_delivery``:
  block until a recipient has *processed* a given message.
- ``delivery_status`` / ``delivery_history`` — inspect the current state and the
  observed transition sequence (e.g. ``[PROCESSING, PROCESSED]``).

Why this is enough: the room processes a room's messages strictly one-at-a-time
in FIFO order (a single per-room process loop), and the agent marks a message
``processed`` only *after* its reply has been emitted. So waiting for the last
message you sent to be processed (its id is returned by ``send_message``) proves
every earlier message was handled *and* that the agent's reply is already in
``messages`` — no probe message and no reply-text matching required.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from band.client.streaming import (
    DeliveryStatus,
    MessageCreatedPayload,
    WebSocketClient,
)

from tests.e2e.helpers import TrackingWebSocketClient

logger = logging.getLogger(__name__)

DEFAULT_DEADLINE_S = 60.0


def _parse_status(raw: Any) -> DeliveryStatus | None:
    """Coerce a raw delivery-status string to ``DeliveryStatus`` (``None`` if
    missing or outside the known set)."""
    try:
        return DeliveryStatus(raw) if raw is not None else None
    except ValueError:
        return None


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
        # message_id -> {recipient_id: delivery-state dict}, fed by
        # ``message_updated`` events. The authoritative "agent finished this
        # message" signal, independent of any LLM reply text.
        self._delivery: dict[str, dict[str, Any]] = {}
        # (message_id, recipient_id) -> ordered, de-duplicated status transitions
        # observed for that pair (e.g. [PROCESSING, PROCESSED]). Lets tests assert
        # on the real lifecycle, not just the final state.
        self._history: dict[tuple[str, str], list[DeliveryStatus]] = defaultdict(list)
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

    def _on_message_updated(self, payload: MessageCreatedPayload) -> None:
        """Record per-recipient delivery state from a ``message_updated`` event."""
        delivery = payload.metadata.delivery_status if payload.metadata else None
        if not delivery:
            return
        # Merge, don't replace: a frame may carry only the recipient that just
        # changed, so overwriting the whole map would wipe other recipients'
        # last-known state (and make delivery_status disagree with the
        # append-only delivery_history below).
        self._delivery.setdefault(payload.id, {}).update(delivery)
        for recipient_id, state in delivery.items():
            status = _parse_status((state or {}).get("status"))
            if status is None:
                continue
            history = self._history[(payload.id, recipient_id)]
            # Record only transitions (the backend may resend the same state).
            if not history or history[-1] is not status:
                history.append(status)
        self._nudge.set()

    def delivery_status(
        self, message_id: str, recipient_id: str
    ) -> DeliveryStatus | None:
        """This recipient's current delivery status for ``message_id``.

        ``None`` until the first ``message_updated`` for the pair arrives (or if
        the backend ever reports a value outside ``DeliveryStatus``).
        """
        recipient = self._delivery.get(message_id, {}).get(recipient_id) or {}
        return _parse_status(recipient.get("status"))

    def delivery_history(
        self, message_id: str, recipient_id: str
    ) -> list[DeliveryStatus]:
        """Ordered, de-duplicated delivery transitions seen for the pair."""
        return list(self._history[(message_id, recipient_id)])

    def _delivery_error(self, message_id: str, recipient_id: str) -> str:
        """Best-effort last-attempt error string, for failure diagnostics."""
        recipient = self._delivery.get(message_id, {}).get(recipient_id) or {}
        attempts = recipient.get("attempts") or []
        last_error = next(
            (a.get("error") for a in reversed(attempts) if a.get("error")), None
        )
        return f" (last error: {last_error})" if last_error else ""

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

    async def wait_for_delivery(
        self,
        message_id: str,
        recipient_id: str,
        *,
        until: Iterable[DeliveryStatus],
        deadline_s: float | None = None,
    ) -> DeliveryStatus:
        """Block until ``recipient_id``'s status for ``message_id`` is one of
        ``until``; return the status reached.

        The general delivery-state waiter. ``wait_for_processed`` is the common
        case built on it; tests can target any state (e.g. ``{FAILED}`` to
        observe a failure, or ``{PROCESSING}`` to catch the in-flight state).
        """
        targets = frozenset(until)
        await self.wait_until(
            lambda _msgs: self.delivery_status(message_id, recipient_id) in targets,
            deadline_s=deadline_s,
        )
        reached = self.delivery_status(message_id, recipient_id)
        assert reached is not None  # the predicate guarantees membership
        return reached

    async def wait_for_processed(
        self, message_id: str, recipient_id: str, *, deadline_s: float | None = None
    ) -> None:
        """Block until ``recipient_id`` has ``PROCESSED`` ``message_id``.

        Driven by ``message_updated`` delivery-state events, not chat text — so
        it is immune to how (or whether) the agent phrases a reply. Per-room FIFO
        means a processed barrier message proves every earlier message was
        processed too, and because ``PROCESSED`` is reported only after the reply
        is emitted, that reply is already in ``messages`` once this returns.

        ``PROCESSED`` is the only success terminal: ``FAILED`` is transient (the
        platform retries), so we wait through it rather than giving up. On
        timeout the error reports the last status seen and any attempt error, so
        a permanently-failing message is diagnosable instead of opaque.
        """
        try:
            await self.wait_for_delivery(
                message_id,
                recipient_id,
                until={DeliveryStatus.PROCESSED},
                deadline_s=deadline_s,
            )
        except TimeoutError:
            last = self.delivery_status(message_id, recipient_id)
            raise TimeoutError(
                f"{recipient_id} did not process message {message_id} in room "
                f"{self.room_id}; last delivery status: "
                f"{last.value if last else 'none'}"
                f"{self._delivery_error(message_id, recipient_id)}"
            ) from None


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

    async def updated_handler(payload: MessageCreatedPayload) -> None:
        capture._on_message_updated(payload)

    await ws.join_chat_room_channel(room_id, handler, updated_handler)
    try:
        yield capture
    finally:
        await ws.leave_chat_room_channel(room_id)
