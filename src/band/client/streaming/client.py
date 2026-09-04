from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
import logging
import random
from typing import Any, Literal

from band_sdk_core import (
    DeadReason,
    Session,
    SessionOutcome,
    SessionPolicy,
    SessionState,
    StaleReason,
)
from phoenix_channels_python_client.client import (
    PHXChannelsClient,
    PhoenixChannelsProtocolVersion,
)
from phoenix_channels_python_client.exceptions import PHXConnectionError
from phoenix_channels_python_client.phx_messages import PHXMessage
from band.client.streaming.errors import (
    WebSocketUpgradeError,
    classify_initial_upgrade_error,
)
from band.client.streaming.watchdog import HeartbeatWatchdog
from band.client.streaming.wire import WirePayload
from band.logging_config import core_issues, trace_context_extra
from band_sdk_core import AgentTopicKind, chat_room_topic, room_participants_topic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebSocketDisconnectReason:
    """Terminal WebSocket disconnect reason reported by the platform."""

    reason: str
    message: str
    retryable: bool
    retry_after: int | None = None
    target_socket_id: str | None = None
    correlation_id: str | None = None
    dead_reason: DeadReason | None = None
    stale_reason: StaleReason | None = None


# WebSocket message payloads (based on actual backend messages)
# Using Pydantic for runtime validation


class Mention(WirePayload):
    """Mention object within message metadata."""

    id: str
    username: str | None = None
    handle: str | None = None
    name: str | None = None


class DeliveryStatus(StrEnum):
    """Per-recipient delivery state for a message (the platform's authoritative,
    LLM-independent processing signal).

    Mirrors the backend's allowed values. The lifecycle for a recipient is
    ``DELIVERED -> PROCESSING -> PROCESSED | FAILED``. ``FAILED`` is **not**
    terminal: the platform retries failed messages (bounded by max retries), so
    a message may cycle ``FAILED -> PROCESSING`` again before reaching
    ``PROCESSED``. ``PROCESSED`` is the only success terminal.
    """

    DELIVERED = "delivered"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class ControlMode(StrEnum):
    """Shared vocabulary for the ``agent.control`` wire signal.

    One typed vocabulary for both sides: ``AgentControlPayload.mode`` (the
    platform's wire field) and ``ExecutionContext.interrupt()``'s ``kind``
    argument. Prevents the two from drifting into separate, disconnected
    enums for the same concept.
    """

    INTERRUPT = "interrupt"
    STOP = "stop"
    PLAY = "play"


class MessageMetadata(WirePayload):
    """Metadata within message_created / message_updated payloads."""

    mentions: list[Mention] = []
    status: str | None = None
    # Per-recipient delivery state, populated on `message_updated` as recipients
    # process the message. Keyed by recipient (agent) id; each value carries a
    # ``status`` (see ``DeliveryStatus``) plus ``current_attempt`` and an
    # ``attempts`` list. This is the same signal the runtime uses to dedup
    # already-handled messages.
    delivery_status: dict[str, Any] | None = None


class MessageCreatedPayload(WirePayload):
    """Payload for message_created events (observed from real WebSocket)."""

    id: str
    content: str
    message_type: str
    metadata: MessageMetadata | None = None
    sender_id: str
    sender_type: str
    sender_name: str | None = None
    chat_room_id: str | None = None
    thread_id: str | None = None
    inserted_at: str
    updated_at: str


class RoomAddedPayload(WirePayload):
    """Payload for room_added events.

    Required/optional fields aligned with the Fern-generated ChatRoom model
    (band_rest.types.chat_room.ChatRoom). The WebSocket may include
    additional fields which are captured by ``extra="allow"``.
    """

    id: str
    inserted_at: str
    updated_at: str
    title: str | None = None
    task_id: str | None = None


class RoomRemovedPayload(WirePayload):
    """Payload for room_removed events.

    band-sdk-core's canonical rule pushes ``room_removed`` through the same
    5-field wire shape as ``room_added`` (``ChatJSON.format_room_event/1``),
    sharing one validator on the Rust side -- so this mirrors
    ``RoomAddedPayload`` field-for-field.
    """

    id: str
    inserted_at: str
    updated_at: str
    title: str | None = None
    task_id: str | None = None


class RoomDeletedPayload(WirePayload):
    """Payload for room_deleted events on room_participants channels."""

    id: str


async def _noop_room_deleted(_: RoomDeletedPayload) -> None:
    return None


class ParticipantAddedPayload(WirePayload):
    """Payload for participant_added events."""

    id: str
    name: str
    type: str
    handle: str | None = None
    description: str | None = None
    is_remote: bool | None = None
    is_external: bool | None = None  # Legacy alias for is_remote


class ParticipantRemovedPayload(WirePayload):
    """Payload for participant_removed events.

    band-sdk-core's canonical rule requires ``name``/``type`` present on the
    wire -- typed here to match what's actually guaranteed post-validation,
    not left to ``extra="allow"`` passthrough.
    """

    id: str
    name: str
    type: str


# Contact event payloads


class ContactRequestReceivedPayload(WirePayload):
    """Payload for contact_request_received events."""

    id: str
    # band-sdk-core's canonical rule accepts these two absent (compact/1 drops
    # them on the wire; see the canonical policy doc's contact_request_received
    # section) -- Optional so from_wire's non-validating hydration never leaves
    # a required field unset (model_construct would, and accessing it raises
    # AttributeError).
    from_handle: str | None = None
    from_name: str | None = None
    message: str | None = None
    status: str
    inserted_at: str


class ContactRequestUpdatedPayload(WirePayload):
    """Payload for contact_request_updated events."""

    id: str
    status: str


class ContactAddedPayload(WirePayload):
    """Payload for contact_added events."""

    id: str
    # band-sdk-core's canonical rule allows an explicit wire `null` for both
    # (the key itself is always present -- see the canonical policy doc's
    # contact_added section), so hydration can deliver a real None here.
    handle: str | None = None
    name: str | None = None
    type: str
    description: str | None = None
    is_remote: bool | None = None
    is_external: bool | None = None  # Legacy alias for is_remote
    inserted_at: str


class ContactRemovedPayload(WirePayload):
    """Payload for contact_removed events."""

    id: str


class AgentControlPayload(WirePayload):
    """Payload for ``agent.control`` events on the agent_control channel.

    Pushed by the platform to interrupt, stop, or resume (play) an agent.
    ``room_id`` is null for agent-scoped fan-out (all of the agent's rooms);
    set for a single (agent, room) target. The server does not deduplicate, so
    consumers should dedup on ``correlation_id``.
    """

    mode: ControlMode
    scope: Literal["agent", "room"]
    agent_id: str
    type: str | None = None
    execution_id: str | None = None
    room_id: str | None = None
    reason: str | None = None
    correlation_id: str | None = None


class SupersedePayload(WirePayload):
    """Payload for terminal agent_control supersede events."""

    reason: str
    message: str
    retryable: bool
    retry_after: int | None = None
    target_socket_id: str | None = None
    correlation_id: str | None = None

    def to_disconnect_reason(
        self, outcome: SessionOutcome | None = None
    ) -> WebSocketDisconnectReason:
        return WebSocketDisconnectReason(
            reason=self.reason,
            message=self.message,
            retryable=self.retryable,
            retry_after=self.retry_after,
            target_socket_id=self.target_socket_id,
            correlation_id=self.correlation_id,
            dead_reason=outcome.dead_reason if outcome is not None else None,
            stale_reason=outcome.stale_reason if outcome is not None else None,
        )


class WireEvent(StrEnum):
    """Every wire event name this SDK recognizes -- the single source of
    truth `_PAYLOAD_MODELS`, `KNOWN_UNHANDLED_EVENTS`, and each `join_*`
    method's handler-dict keys are keyed from, instead of each repeating the
    string literal. A member is still a plain ``str``, so it passes straight
    through to `from_wire`/`band_sdk_core` unchanged. Members through
    `AGENT_CONTROL` mirror `band_sdk_core.EventType`'s wire-name vocabulary
    (kept as literals, not derived, since `EventType` is an opaque PyO3 type
    that can't be a `StrEnum` member's value; a drift-guard test in
    `tests/websocket/test_client.py` keeps the two in sync).
    `TASK_CREATED`/`TASK_UPDATED` are outside it entirely (the `tasks:*`
    channel's raw-dict passthrough never calls `validate_event_payload`, and
    has no band-sdk-typescript counterpart to justify a core module).
    """

    MESSAGE_CREATED = "message_created"
    # Shares message_created's shape; the delivery-state transitions live in
    # ``metadata.delivery_status``.
    MESSAGE_UPDATED = "message_updated"
    ROOM_ADDED = "room_added"
    ROOM_REMOVED = "room_removed"
    ROOM_DELETED = "room_deleted"
    PARTICIPANT_ADDED = "participant_added"
    PARTICIPANT_REMOVED = "participant_removed"
    CONTACT_REQUEST_RECEIVED = "contact_request_received"
    CONTACT_REQUEST_UPDATED = "contact_request_updated"
    CONTACT_ADDED = "contact_added"
    CONTACT_REMOVED = "contact_removed"
    SUPERSEDE = "supersede"
    AGENT_CONTROL = "agent.control"
    # No PlatformEvent/payload model anywhere in the codebase -- event rows
    # (thought/error/task/tool_call/tool_result) are read back over REST
    # instead (see tests/e2e/baseline/toolkit/observations/tool_calls.py), so
    # this is expected, not a bug. Any other unregistered event name still warns.
    EVENT_CREATED = "event_created"
    # `tasks:*` channel only -- no payload model, raw dict passthrough.
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"


_PAYLOAD_MODELS: dict[WireEvent, type[WirePayload]] = {
    WireEvent.MESSAGE_CREATED: MessageCreatedPayload,
    WireEvent.MESSAGE_UPDATED: MessageCreatedPayload,
    WireEvent.ROOM_ADDED: RoomAddedPayload,
    WireEvent.ROOM_REMOVED: RoomRemovedPayload,
    WireEvent.ROOM_DELETED: RoomDeletedPayload,
    WireEvent.PARTICIPANT_ADDED: ParticipantAddedPayload,
    WireEvent.PARTICIPANT_REMOVED: ParticipantRemovedPayload,
    WireEvent.CONTACT_REQUEST_RECEIVED: ContactRequestReceivedPayload,
    WireEvent.CONTACT_REQUEST_UPDATED: ContactRequestUpdatedPayload,
    WireEvent.CONTACT_ADDED: ContactAddedPayload,
    WireEvent.CONTACT_REMOVED: ContactRemovedPayload,
    WireEvent.SUPERSEDE: SupersedePayload,
    WireEvent.AGENT_CONTROL: AgentControlPayload,
}


KNOWN_UNHANDLED_EVENTS = frozenset({WireEvent.EVENT_CREATED})


def _disconnect_reason_from_exception(
    exc: Exception, outcome: SessionOutcome
) -> WebSocketDisconnectReason:
    """Synthesize a WebSocketDisconnectReason for an initial-connect failure
    Session has classified as terminal. Unlike the supersede path, there is
    no platform wire payload here -- reason/message are derived from the
    raised exception itself, not a server-supplied constant."""
    session_fields = {
        "dead_reason": outcome.dead_reason,
        "stale_reason": outcome.stale_reason,
    }
    if isinstance(exc, WebSocketUpgradeError):
        return WebSocketDisconnectReason(
            reason=exc.code or f"http_{exc.status_code}",
            message=exc.message,
            retryable=False,
            retry_after=exc.retry_after,
            correlation_id=exc.request_id,
            **session_fields,
        )
    return WebSocketDisconnectReason(
        reason="connection_failed",
        message=str(exc),
        retryable=False,
        **session_fields,
    )


class WebSocketClient:
    def __init__(
        self,
        ws_url: str,
        api_key: str,
        agent_id: str | None = None,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
        on_disconnect: Callable[[Exception | None], Awaitable[None]] | None = None,
        session_policy: SessionPolicy | None = None,
    ):
        self.ws_url = ws_url
        self.api_key = api_key
        self.agent_id = agent_id
        self.client: PHXChannelsClient | None = None
        self._on_reconnect = on_reconnect
        self._on_disconnect = on_disconnect
        self._validation_error_count: int = 0
        self._last_disconnect_reason: WebSocketDisconnectReason | None = None
        self._watchdog = HeartbeatWatchdog(session_policy or SessionPolicy.default())
        self._session = Session(self._watchdog.policy)

    @property
    def validation_error_count(self) -> int:
        """Number of events dropped due to payload validation errors."""
        return self._validation_error_count

    @property
    def last_disconnect_reason(self) -> WebSocketDisconnectReason | None:
        """Most recent terminal disconnect reason reported by the platform."""
        return self._last_disconnect_reason

    def reset_validation_error_count(self) -> int:
        """Reset the validation error counter and return the previous value.

        Useful for periodic metric flushes (non-atomic, safe for single event loop).
        """
        count = self._validation_error_count
        self._validation_error_count = 0
        return count

    def _require_client(self) -> PHXChannelsClient:
        if self.client is None:
            raise RuntimeError("WebSocket client is not connected")
        return self.client

    def joined_topics(self) -> frozenset[str]:
        """Live snapshot of the transport's subscription registry. Call
        once per detection pass and check membership against it --
        `get_current_subscriptions` copies its dict on every call."""
        return frozenset(self._require_client().get_current_subscriptions())

    async def _handle_reconnect(self) -> None:
        """Reset the watchdog deadline on reconnect, then forward to the
        caller's own on_reconnect callback (if any).

        Without this, a reconnect can inherit a deadline set before the new
        socket existed and expire before its first heartbeat_interval_s
        cycle completes, force-closing a healthy connection.
        """
        self._watchdog.reset_deadline()
        if self._on_reconnect is not None:
            await self._on_reconnect()

    def _build_phx_client(self) -> PHXChannelsClient:
        """Construct a fresh PHXChannelsClient for one connection attempt --
        a new instance is required per attempt; the vendored client has no
        reconnect-from-here primitive."""
        client = PHXChannelsClient(
            self.ws_url,
            self.api_key,
            protocol_version=PhoenixChannelsProtocolVersion.V2,
            auto_reconnect=False,
            heartbeat_interval_s=self._watchdog.policy.heartbeat_interval_s,
            on_reconnect=self._handle_reconnect,
            on_disconnect=self._on_disconnect,
            on_heartbeat_ack=self._watchdog.reset_deadline,
            # Also send the key as an x-api-key handshake header. Under
            # proxy-managed sandbox custody the host-side proxy replaces the
            # sentinel in this header (it can't touch the URL query), and the
            # platform authenticates off the header (precedence over the
            # query) — so the WS upgrade works with the real key never in the
            # VM. Harmless elsewhere: same value the query already carries.
            additional_headers={"x-api-key": self.api_key},
        )
        if self.agent_id:
            client.channel_socket_url += f"&agent_id={self.agent_id}"
        return client

    async def _resolve_failed_connect_attempt(
        self, exc: Exception, epoch: int
    ) -> float:
        """Classify one failed initial-connect attempt through Session and
        resolve it to a retry delay, or raise if Session now considers the
        session Dead."""
        # Captured before the live-socket probe below (up to open_timeout=5s
        # inside classify_initial_upgrade_error) -- charging the probe's own
        # latency to Session's rapid-disconnect timing would understate how
        # fast repeated failures are actually happening.
        now = asyncio.get_running_loop().time()
        upgrade_error = await classify_initial_upgrade_error(
            exc, self._require_client().channel_socket_url
        )
        match upgrade_error:
            case WebSocketUpgradeError():
                outcome = self._session.on_upgrade_rejected(
                    epoch,
                    now,
                    upgrade_error.status_code,
                    upgrade_error.retry_after,
                    random.random(),
                )
                raise_exc: Exception = upgrade_error
            case None if isinstance(exc, PHXConnectionError):
                outcome = self._session.on_socket_close(
                    epoch, now, None, random.random()
                )
                raise_exc = exc
            case _:
                raise

        if outcome.state is SessionState.Dead:
            self.record_terminal_disconnect(
                _disconnect_reason_from_exception(raise_exc, outcome)
            )
            logger.warning(
                "Initial WebSocket connection permanently failed (dead_reason=%s): %s",
                outcome.dead_reason,
                raise_exc,
            )
            if raise_exc is exc:
                # Re-raising the exception already being handled -- no new
                # object, so no chaining to add (bare raise).
                raise raise_exc
            # A newly-built WebSocketUpgradeError -- chain it to the
            # original exception, same as the pre-Session code did.
            raise raise_exc from exc

        # None only when Dead (handled above) or stale (unreachable here) --
        # fail loudly rather than trust a Session contract that assert would
        # silently drop under python -O.
        if outcome.retry_after_s is None:
            raise RuntimeError(
                "Session returned a non-Dead outcome with no retry_after_s"
            )
        logger.warning(
            "Initial WebSocket connection failed; retrying in %.2fs: %s",
            outcome.retry_after_s,
            raise_exc,
        )
        return outcome.retry_after_s

    def _finalize_successful_connect(self, epoch: int) -> None:
        """Confirm a successful connect with Session, then hand ongoing
        reconnect timing to the vendored client's own auto_reconnect loop --
        untouched by Session (see the out-of-scope note in the design doc)."""
        now = asyncio.get_running_loop().time()
        connected = self._session.on_connected(epoch, now)
        if connected.stale_reason is not None:
            # Session's contract allows a stale outcome here even though it
            # can't occur on a synchronous success -- log, nothing to do
            # (the connect already succeeded).
            logger.warning(
                "Session.on_connected reported a stale outcome "
                "(stale_reason=%s) for a synchronously-successful connect; "
                "proceeding anyway.",
                connected.stale_reason,
            )
        client = self._require_client()
        client.auto_reconnect = True
        self._watchdog.start(client)

    async def __aenter__(self):
        """Create and enter the PHXChannelsClient context.

        Backoff/retry timing for this *initial*-connect loop is driven by
        ``self._session``: it decides, on every failure, whether to sleep and
        retry (``Reconnecting``) or give up for good (``Dead``). Once this
        loop returns successfully, ongoing reconnect timing passes to the
        vendored PHXChannelsClient's own ``auto_reconnect`` loop -- untouched
        by ``Session`` (see the out-of-scope note in the design doc).

        A fresh ``Session`` is built for every entry, mirroring the fresh
        ``PHXChannelsClient`` built below -- ``__aexit__`` ends the previous
        one, so reusing this instance across sequential ``async with``
        blocks (as callers were free to do before ``Session`` existed) must
        start a new connection lifecycle, not resume a now-Dead one.
        """
        self._session = Session(self._watchdog.policy)
        while True:
            now = asyncio.get_running_loop().time()
            epoch = self._session.begin_attempt(now)
            if epoch is None:
                # Unreachable given this loop's own control flow, but
                # Session's signature allows it -- fail loudly rather than
                # silently proceed with a None epoch, and still populate
                # last_disconnect_reason like every other Dead path in this
                # class so BandLink.connect() sees a terminal reason too.
                self.record_terminal_disconnect(
                    WebSocketDisconnectReason(
                        reason="session_ended",
                        message="WebSocket session is no longer connectable",
                        retryable=False,
                    )
                )
                raise RuntimeError("WebSocket session is no longer connectable")

            self.client = self._build_phx_client()

            try:
                await self.client.__aenter__()
            except Exception as exc:
                delay = await self._resolve_failed_connect_attempt(exc, epoch)
                await asyncio.sleep(delay)
            else:
                self._finalize_successful_connect(epoch)
                return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the PHXChannelsClient context"""
        await self._watchdog.stop()
        self._session.end()
        if self.client:
            await self.client.__aexit__(exc_type, exc_val, exc_tb)

    async def _handle_events(self, message: PHXMessage, event_handlers: dict):
        """Generic async event handler that maps events to their corresponding async callbacks"""
        logger.debug("[WebSocket] Received event: %s", message.event)

        # Check if we have a handler for this event
        if message.event not in event_handlers:
            level = (
                logging.DEBUG
                if message.event in KNOWN_UNHANDLED_EVENTS
                else logging.WARNING
            )
            logger.log(
                level,
                "[WebSocket] Received event '%s' but no handler registered. "
                "Available handlers: %s",
                message.event,
                list(event_handlers.keys()),
            )
            return

        # Validate (band-sdk-core) and hydrate into typed payload models for
        # known event types.
        model = _PAYLOAD_MODELS.get(message.event)
        if model is not None:
            try:
                validated = model.from_wire(message.event, message.payload)
            except ValueError as e:
                # band-sdk-core rejected the payload; `.issues` carries every
                # violation. This log line runs outside any
                # trace_context_scope() (validation happens in the transport
                # layer, before a turn exists), so the ambient TRACE_CONTEXT
                # would be None here -- extra=trace_context_extra(e) reports
                # `e`'s own traceparent instead, via the same record attribute
                # _TraceContextFilter would otherwise fill in.
                issues = core_issues(e)
                errors = (
                    "; ".join(f"{path}: {msg}" for path, _code, msg in issues)
                    if issues
                    else str(e)
                )
                logger.error(
                    "[WebSocket] Invalid %s payload: %s",
                    message.event,
                    errors,
                    extra=trace_context_extra(e),
                )
                logger.debug(
                    "[WebSocket] Raw payload for invalid %s: %s",
                    message.event,
                    message.payload,
                )
                self._validation_error_count += 1
                return
            except (TypeError, AttributeError):
                # Payload passed band-sdk-core but hydration couldn't build a
                # well-shaped model from it -- a gap between what band-sdk-core
                # accepts and this SDK's typed projection, not routine bad wire
                # data, so it's logged distinctly (with a traceback) rather
                # than blended into the ValueError case above. Still counted
                # and dropped, protecting the event loop the same way the
                # callback invocation below does.
                logger.exception(
                    "[WebSocket] %s payload passed band-sdk-core but failed to "
                    "hydrate -- likely a gap between band-sdk-core's rules and "
                    "this SDK's typed model",
                    message.event,
                )
                logger.debug(
                    "[WebSocket] Raw payload for unhydratable %s: %s",
                    message.event,
                    message.payload,
                )
                self._validation_error_count += 1
                return
        else:
            # Unknown event types: pass the raw payload dict
            validated = message.payload

        callback = event_handlers[message.event]
        if callback:
            try:
                await callback(validated)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 – intentionally broad to protect event loop
                logger.exception(
                    "[WebSocket] Callback error for %s event", message.event
                )

    def record_terminal_disconnect(self, reason: WebSocketDisconnectReason) -> None:
        """Record a terminal platform disconnect reason and disable reconnect."""
        self._last_disconnect_reason = reason
        self.disable_reconnect()

    def disable_reconnect(self) -> None:
        """Disable PHX auto-reconnect for a terminal platform disconnect."""
        if self.client:
            self.client.auto_reconnect = False

    def handle_supersede(
        self, retryable: bool, retry_after_s: float | None
    ) -> SessionOutcome:
        """Arbitrate an agent_control supersede event through Session --
        whether it's actually current (not a stale notification about an
        epoch this connection has already moved past) and, in principle,
        whether a retryable supersede should keep reconnecting."""
        now = asyncio.get_running_loop().time()
        return self._session.on_supersede(
            now, retryable, retry_after_s, random.random()
        )

    async def join_agent_control_channel(
        self,
        agent_id: str,
        on_supersede: Callable[[SupersedePayload], Awaitable[None]],
        on_control: Callable[[AgentControlPayload], Awaitable[None]] | None = None,
    ):
        """Subscribe to agent-control events for this agent.

        Handles terminal ``supersede`` events and, when ``on_control`` is
        provided, ``agent.control`` interrupt/stop/play signals.
        """
        topic = AgentTopicKind.Control.topic(agent_id)
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        handlers: dict[str, Callable[..., Awaitable[None]]] = {
            WireEvent.SUPERSEDE: on_supersede
        }
        if on_control is not None:
            handlers[WireEvent.AGENT_CONTROL] = on_control

        async def message_handler(message):
            await self._handle_events(message, handlers)

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def join_agent_rooms_channel(
        self,
        agent_id: str,
        on_room_added: Callable[[RoomAddedPayload], Awaitable[None]],
        on_room_removed: Callable[[RoomRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to agent rooms topic with async callbacks"""
        topic = AgentTopicKind.Rooms.topic(agent_id)
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    WireEvent.ROOM_ADDED: on_room_added,
                    WireEvent.ROOM_REMOVED: on_room_removed,
                },
            )

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def join_chat_room_channel(
        self,
        chat_room_id: str,
        on_message_created: Callable[[MessageCreatedPayload], Awaitable[None]],
        on_message_updated: Callable[[MessageCreatedPayload], Awaitable[None]]
        | None = None,
    ):
        """Subscribe to chat room topic for message events with async callbacks.

        ``on_message_updated`` is optional; when provided it receives
        ``message_updated`` events (e.g. delivery-status transitions). Omit it to
        ignore those events as before.
        """
        topic = chat_room_topic(chat_room_id)
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        handlers: dict[str, Callable[[MessageCreatedPayload], Awaitable[None]]] = {
            WireEvent.MESSAGE_CREATED: on_message_created
        }
        if on_message_updated is not None:
            handlers[WireEvent.MESSAGE_UPDATED] = on_message_updated

        async def message_handler(message):
            await self._handle_events(message, handlers)

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_user_rooms_channel(
        self,
        user_id: str,
        on_room_added: Callable[[RoomAddedPayload], Awaitable[None]],
        on_room_removed: Callable[[RoomRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to user rooms topic with async callbacks"""
        topic = f"user_rooms:{user_id}"

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    WireEvent.ROOM_ADDED: on_room_added,
                    WireEvent.ROOM_REMOVED: on_room_removed,
                },
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_room_participants_channel(
        self,
        chat_room_id: str,
        on_participant_added: Callable[[ParticipantAddedPayload], Awaitable[None]],
        on_participant_removed: Callable[[ParticipantRemovedPayload], Awaitable[None]],
        on_room_deleted: Callable[
            [RoomDeletedPayload], Awaitable[None]
        ] = _noop_room_deleted,
    ):
        """Subscribe to room participants topic with async callbacks"""
        topic = room_participants_topic(chat_room_id)
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    WireEvent.PARTICIPANT_ADDED: on_participant_added,
                    WireEvent.PARTICIPANT_REMOVED: on_participant_removed,
                    WireEvent.ROOM_DELETED: on_room_deleted,
                },
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_tasks_channel(
        self,
        user_id: str,
        on_task_created: Callable[[dict], Awaitable[None]],
        on_task_updated: Callable[[dict], Awaitable[None]],
    ):
        """Subscribe to tasks topic with async callbacks"""
        topic = f"tasks:{user_id}"

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    WireEvent.TASK_CREATED: on_task_created,
                    WireEvent.TASK_UPDATED: on_task_updated,
                },
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def leave_agent_control_channel(self, agent_id: str):
        """Unsubscribe from agent control topic"""
        topic = AgentTopicKind.Control.topic(agent_id)
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_agent_rooms_channel(self, agent_id: str):
        """Unsubscribe from agent rooms topic"""
        topic = AgentTopicKind.Rooms.topic(agent_id)
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_chat_room_channel(self, chat_room_id: str):
        """Unsubscribe from chat room topic"""
        topic = chat_room_topic(chat_room_id)
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_user_rooms_channel(self, user_id: str):
        """Unsubscribe from user rooms topic"""
        topic = f"user_rooms:{user_id}"
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_room_participants_channel(self, chat_room_id: str):
        """Unsubscribe from room participants topic"""
        topic = room_participants_topic(chat_room_id)
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_tasks_channel(self, user_id: str):
        """Unsubscribe from tasks topic"""
        topic = f"tasks:{user_id}"
        return await self._require_client().unsubscribe_from_topic(topic)

    async def join_agent_contacts_channel(
        self,
        agent_id: str,
        on_contact_request_received: Callable[
            [ContactRequestReceivedPayload], Awaitable[None]
        ],
        on_contact_request_updated: Callable[
            [ContactRequestUpdatedPayload], Awaitable[None]
        ],
        on_contact_added: Callable[[ContactAddedPayload], Awaitable[None]],
        on_contact_removed: Callable[[ContactRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to agent contacts topic with async callbacks."""
        topic = AgentTopicKind.Contacts.topic(agent_id)
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    WireEvent.CONTACT_REQUEST_RECEIVED: on_contact_request_received,
                    WireEvent.CONTACT_REQUEST_UPDATED: on_contact_request_updated,
                    WireEvent.CONTACT_ADDED: on_contact_added,
                    WireEvent.CONTACT_REMOVED: on_contact_removed,
                },
            )

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def leave_agent_contacts_channel(self, agent_id: str):
        """Unsubscribe from agent contacts topic."""
        topic = AgentTopicKind.Contacts.topic(agent_id)
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def run_forever(self):
        await self._require_client().run_forever()
