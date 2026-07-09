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
  block until a recipient has *processed* a given message. Use it before reading
  *durable* turn state (memory, tool calls, usage, events) — persisted by the time a
  message is processed.
- ``wait_for_reply`` — the reply barrier: block until the turn is processed *and* the
  agent's reply frame has actually been captured. Use it (not ``wait_for_processed``)
  before asserting on captured reply text — the reply's ``message_created`` frame and
  the delivery-status ``message_updated`` frame are independent, unordered platform
  events, so the buffer can lag the processed signal.
- ``delivery_status`` / ``delivery_history`` — inspect the current state and the
  observed transition sequence (e.g. ``[PROCESSING, PROCESSED]``).

Why this is enough: the room processes a room's messages strictly one-at-a-time
in FIFO order (a single per-room process loop), and the agent marks a message
``processed`` when its handler completes. So waiting for the last message you sent
to be processed (its id is returned by ``send_message``) proves every earlier
message was handled — no probe message and no reply-text matching required. Note:
``processed`` does **not** imply a reply was emitted — replies are optional (the LLM
may not call ``band_send_message``), so never infer reply presence from the barrier.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from band_rest import AsyncRestClient

from band.client.streaming import (
    DeliveryStatus,
    MessageCreatedPayload,
    WebSocketClient,
)
from band.core.types import MessageType

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.observations import (
    Errors,
    Events,
    Memories,
    MemoryObservation,
    MemoryToolCalls,
    Replies,
    Tasks,
    Thoughts,
    ToolCalls,
    Usage,
)
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    agent_rest_client,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

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

    def __init__(
        self,
        room_id: str,
        *,
        user_ops: UserOps | None = None,
        settings: BaselineSettings | None = None,
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> None:
        self.room_id = room_id
        self.deadline_s = deadline_s  # default failure deadline for waits
        self._user_ops = user_ops
        self._settings = settings
        # One agent-auth REST client per agent id, reused across memory reads.
        self._agent_clients: dict[str, AsyncRestClient] = {}
        self.messages: Replies = Replies()
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

    def turn_boundary(self) -> datetime:
        """Server timestamp of the latest captured reply — a between-turns boundary.

        Use it as ``since`` for a later durable read (``tool_calls`` / ``usage``) so
        that read is scoped to the *next* turn, even across a reused capture or a
        stop/restart (the value is a plain timestamp that carries over the capture's
        lifetime). Naive timestamps are treated as UTC (the platform stores UTC),
        matching the orphan-sweep coercion. Call it after a completion barrier: it
        raises if no reply has been captured yet (an empty buffer would otherwise
        surface as an opaque ``IndexError``).
        """
        if not self.messages:
            raise RuntimeError(
                "turn_boundary() needs a captured reply; call it after wait_for_processed"
            )
        # Normalize a trailing Z before parsing, matching the src/band convention.
        raw = self.messages[-1].inserted_at.replace("Z", "+00:00")
        stamp = datetime.fromisoformat(raw)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

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
        processed too. This proves the *turn* finished and its durable state is
        persisted; it does **not** prove the reply's ``message_created`` frame has
        been captured — that frame is an independent, unordered platform event, so
        to assert on reply text wait on ``wait_for_reply`` instead.

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

    async def wait_for_reply(
        self,
        message_id: str,
        recipient_id: str,
        *,
        sender_id: str | None = None,
        since: int = 0,
        deadline_s: float | None = None,
    ) -> Replies:
        """Block until ``recipient_id`` has PROCESSED ``message_id`` *and* a captured
        reply (optionally only ``sender_id``'s, past cursor ``since``) is present;
        return that reply window.

        The reply barrier — use it instead of ``wait_for_processed`` whenever the test
        then asserts on captured reply *text*. ``wait_for_processed`` only proves the
        turn finished: the reply arrives as a ``message_created`` frame the platform
        emits *independently* of the delivery-status ``message_updated`` frame (a
        different row, a different write), with no cross-frame ordering guarantee. So
        the PROCESSED frame can reach the observer a beat before the reply's frame, and
        reading ``messages`` the instant ``wait_for_processed`` returns races that gap
        and can see an empty buffer. Waiting on the reply itself closes the race
        deterministically, and is event-driven — the deadline is a *failure* bound, not
        a success signal.

        Pass ``sender_id`` when several agents post into the room (e.g. a peer whose own
        message is captured too) to wait for a *specific* author's reply. Pair with
        ``snapshot()`` across a reused capture: pass the pre-send cursor as ``since`` so
        only this turn's replies count. On timeout the error says whether the turn never
        finished (stuck) or finished without the expected reply (silent), so a failure is
        diagnosable rather than a bare empty buffer.
        """

        def window() -> Replies:
            replies = self.messages.since(since)
            return replies.from_sender(sender_id) if sender_id is not None else replies

        def ready(_messages: list[MessageCreatedPayload]) -> bool:
            return self.delivery_status(
                message_id, recipient_id
            ) == DeliveryStatus.PROCESSED and bool(window())

        try:
            await self.wait_until(ready, deadline_s=deadline_s)
        except TimeoutError:
            status = self.delivery_status(message_id, recipient_id)
            if status == DeliveryStatus.PROCESSED:
                who = f" from {sender_id}" if sender_id is not None else ""
                raise TimeoutError(
                    f"{recipient_id} processed message {message_id} in room "
                    f"{self.room_id} but no reply{who} was captured"
                ) from None
            raise TimeoutError(
                f"{recipient_id} did not process message {message_id} in room "
                f"{self.room_id}; last delivery status: "
                f"{status.value if status else 'none'}"
                f"{self._delivery_error(message_id, recipient_id)}"
            ) from None
        return window()

    def _require_user_ops(self) -> UserOps:
        """Return the bound ``UserOps`` or raise (durable reads need it).

        The calling method's name is read from the stack frame (the single source
        of truth) so the error names it without each caller passing a literal.
        """
        if self._user_ops is None:
            caller = sys._getframe(1).f_code.co_name
            raise RuntimeError(
                f"ReplyCapture.{caller} needs user_ops; use the reply_capture fixture"
            )
        return self._user_ops

    async def tool_calls(
        self,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        include_memory: bool = False,
    ) -> ToolCalls:
        """Read this room's tool calls (call after the turn settles).

        Reads the persisted ``tool_call`` events, so the agent must run with
        execution reporting on, and this should follow a completion barrier such
        as ``wait_for_processed`` (the platform marks a message ``processed``
        only after the reply is emitted, by which point the turn's tool-call
        events are already persisted). Pass ``sender_id`` to keep only one
        agent's calls. Memory tools are excluded by default; pass
        ``include_memory=True`` to keep them, or use ``memory(agent)`` for the
        dedicated two-layer memory view.

        Without ``since`` this returns *every* tool call in the room — which is
        the turn only when the capture spans a single turn. When reusing a
        capture across turns, pass ``since`` (a server timestamp, e.g. the
        ``inserted_at`` of the last message before the turn) to exclude earlier
        turns' calls.
        """
        return await ToolCalls.read(
            self._require_user_ops(),
            self.room_id,
            sender_id=sender_id,
            since=since,
            limit=limit,
            include_memory=include_memory,
        )

    async def usage(
        self,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Usage:
        """Read this room's per-turn token usage (call after the turn settles).

        Reads the persisted ``usage`` events, so the agent must run with
        ``Emit.USAGE`` on, and this should follow a completion barrier such as
        ``wait_for_processed`` (the platform marks a message ``processed`` only
        after the reply is emitted, by which point the turn's usage event is
        already persisted). Pass ``sender_id`` to keep only one agent's usage.

        Comes back empty for an adapter that cannot report usage (server-side
        execution) — the honest N-A. Without ``since`` this returns *every*
        usage record in the room; pass ``since`` (a server timestamp) to scope
        to a single turn when reusing a capture across turns.
        """
        return await Usage.read(
            self._require_user_ops(),
            self.room_id,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )

    async def memory(
        self,
        agent: ProvisionedAgent,
        *,
        since: datetime | None = None,
        limit: int = 100,
        subject_id: str | None = None,
        scope: Any | None = None,
        system: Any | None = None,
        type: Any | None = None,
        segment: Any | None = None,
        content_query: str | None = None,
        status: Any | None = None,
    ) -> MemoryObservation:
        """Read both layers of ``agent``'s memory for the turn (after the barrier).

        Returns a :class:`MemoryObservation` with ``.calls`` (the *call* layer:
        which memory tools the agent invoked, from the room's ``tool_call`` events)
        and ``.stored`` (the *store* layer: which records actually landed, from the
        memories API via the agent's own key -- hence the ``agent`` arg).

        The ``scope``/``system``/``type``/``segment``/``content_query``/``status``
        filters narrow the store read. ``limit`` caps both layers (call events and
        stored records). Needs ``user_ops`` and ``settings`` (both bound by the
        ``reply_capture`` fixture); the agent must run with ``Emit.EXECUTION`` for
        the call layer to be populated.
        """
        user_ops = self._require_user_ops()
        if self._settings is None:
            raise RuntimeError(
                "ReplyCapture.memory needs settings; use the reply_capture fixture"
            )
        client = self._agent_clients.get(agent.id)
        if client is None:
            client = agent_rest_client(agent, self._settings)
            self._agent_clients[agent.id] = client
        # The two layers hit different clients/endpoints (room events via the
        # observer client, the store via the agent client), so read them
        # concurrently rather than paying both round-trips in series.
        calls, stored = await asyncio.gather(
            MemoryToolCalls.read(
                user_ops, self.room_id, sender_id=agent.id, since=since, limit=limit
            ),
            Memories.read(
                client,
                subject_id=subject_id,
                scope=scope,
                system=system,
                type=type,
                segment=segment,
                content_query=content_query,
                status=status,
                limit=limit,
            ),
        )
        return MemoryObservation(calls=calls, stored=stored)

    async def events(
        self,
        message_type: MessageType,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Events:
        """Read this room's emitted events of ``message_type`` (call after the turn
        settles). Same read contract as ``tool_calls``; see ``thoughts``/``errors``/
        ``tasks`` for the named conveniences."""
        return await Events.read(
            self._require_user_ops(),
            self.room_id,
            message_type=message_type,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )

    async def thoughts(
        self,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Thoughts:
        """Read this room's ``thought`` events (call after the turn settles)."""
        return await Thoughts.read(
            self._require_user_ops(),
            self.room_id,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )

    async def errors(
        self,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Errors:
        """Read this room's ``error`` events (call after the turn settles)."""
        return await Errors.read(
            self._require_user_ops(),
            self.room_id,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )

    async def tasks(
        self,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Tasks:
        """Read this room's ``task`` events (call after the turn settles)."""
        return await Tasks.read(
            self._require_user_ops(),
            self.room_id,
            sender_id=sender_id,
            since=since,
            limit=limit,
        )


@asynccontextmanager
async def reply_capture(
    ws: WebSocketClient | TrackingWebSocketClient,
    room_id: str,
    *,
    user_ops: UserOps | None = None,
    settings: BaselineSettings | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> AsyncIterator[ReplyCapture]:
    """Subscribe to a room before sending, yield a capture, leave on exit."""
    capture = ReplyCapture(
        room_id, user_ops=user_ops, settings=settings, deadline_s=deadline_s
    )

    async def handler(payload: MessageCreatedPayload) -> None:
        capture._on_message(payload)

    async def updated_handler(payload: MessageCreatedPayload) -> None:
        capture._on_message_updated(payload)

    await ws.join_chat_room_channel(room_id, handler, updated_handler)
    try:
        yield capture
    finally:
        await ws.leave_chat_room_channel(room_id)


# Type of the ``reply_capture`` fixture: call with a room id, get a
# ``ReplyCapture`` async context manager. Shared so smokes type their fixture
# parameter without each redefining the same alias.
CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]
