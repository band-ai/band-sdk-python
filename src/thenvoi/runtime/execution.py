"""
Execution - Per-room execution interface and default implementation.

Extracted from AgentSession with simplified interface.

Crash Recovery:
    When an agent restarts, it may have missed messages while down.
    The sync mechanism handles this:
    1. First WebSocket message marks the sync point (_first_ws_msg_id)
    2. Before processing WS queue, _synchronize_with_next() polls REST API
    3. Process backlog messages until we reach the sync point
    4. Clear marker and continue with WebSocket queue
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    runtime_checkable,
)

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    PlatformEvent,
)

from .types import (
    ConversationContext,
    PlatformMessage,
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
    SYNTHETIC_SENDER_TYPE,
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
)
from .retry_tracker import MessageRetryTracker
from ._context_serialization import context_item_to_dict

if TYPE_CHECKING:
    from thenvoi.platform.link import ThenvoiLink

logger = logging.getLogger(__name__)


class _ResyncRequest:
    """Sentinel pushed into the execution queue to trigger an immediate /next resync.

    Used by request_resync() so a reconnect signal wakes the Phase 2 loop
    without waiting for the idle-timeout to expire.
    """

    type: str = "_resync"  # Matches the .type attribute pattern of PlatformEvent


def _error_label(e: Exception) -> str:
    """Return a non-empty label for an exception, falling back to the class name."""
    return str(e).strip() or type(e).__name__


@runtime_checkable
class Execution(Protocol):
    """
    Interface for per-room execution. Pluggable.

    Implementations handle what happens INSIDE a room.
    The default ExecutionContext uses context accumulation.
    Custom implementations (e.g., Letta) can use persistent agents.

    .. versionchanged:: 0.2.0
        Breaking change: The ``stop()`` method signature changed from
        ``async def stop() -> None`` to ``async def stop(timeout=None) -> bool``.

    Migration Guide:
        If you have a custom Execution implementation, update the stop() method:

        Before::

            async def stop(self) -> None:
                # cleanup logic
                pass

        After::

            async def stop(self, timeout: float | None = None) -> bool:
                # cleanup logic (timeout can be ignored if not needed)
                return True  # Return True for graceful, False if interrupted
    """

    room_id: str

    async def start(self) -> None:
        """Start the execution context."""
        ...

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop the execution context.

        Args:
            timeout: Optional seconds to wait for graceful shutdown.
                     None means stop immediately.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        ...

    def inject_system_message(self, message: str) -> None:
        """
        Queue a system message for injection on next processing.

        Used by ContactEventHandler to broadcast contact changes.
        """
        ...

    async def on_event(self, event: PlatformEvent) -> None:
        """Handle a platform event for this room."""
        ...

    async def request_resync(self) -> None:
        """Signal the process loop to re-poll /next immediately.

        .. versionchanged:: 0.2.0
            Custom ``Execution`` implementations should add ``request_resync()``.
            ``AgentRuntime`` falls back safely for legacy implementations that do
            not provide it, but typed protocol conformance now includes this method.

        Called after WebSocket reconnect to catch messages that arrived while
        the socket was down. Custom implementations may provide a no-op.
        """
        ...


# Type for execution callback
ExecutionHandler = Callable[["ExecutionContext", PlatformEvent], Awaitable[None]]


class ExecutionContext:
    """
    Default execution: context accumulation model.

    Extracted from AgentSession.

    - Accumulates inputs (history, participants)
    - Queues messages
    - Feeds agent when instantiated
    - Agent disappears after execution

    Example:
        async def on_execute(ctx: ExecutionContext, event: PlatformEvent):
            if isinstance(event, MessageEvent):
                tools = AgentTools.from_context(ctx)
                history = ctx.get_history_for_llm()
                # Run LLM with context and tools...

        ctx = ExecutionContext(room_id, link, on_execute)
        await ctx.start()
    """

    def __init__(
        self,
        room_id: str,
        link: "ThenvoiLink",
        on_execute: ExecutionHandler,
        config: SessionConfig | None = None,
        agent_id: str | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
        *,
        hub_room_id: str | None = None,
    ):
        """
        Initialize execution context for a specific room.

        Args:
            room_id: The room this context manages
            link: ThenvoiLink for REST API calls
            on_execute: Callback for handling events
            config: Optional session configuration
            agent_id: Agent ID for filtering self-messages
            on_participant_added: Optional callback for participant_added events
            on_participant_removed: Optional callback for participant_removed events
            hub_room_id: Optional hub-room ID. Forwarded to AgentTools so the
                schema methods can auto-enable contact tools when this context
                belongs to the hub room.
        """
        self.room_id = room_id
        self.link = link
        self._on_execute = on_execute
        self.config = config or SessionConfig()
        self._agent_id = agent_id
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed
        self.hub_room_id = hub_room_id

        # Per-room state
        self.queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        self.state: Literal["starting", "idle", "processing"] = "starting"
        self._is_running = False
        self._process_loop_task: asyncio.Task[None] | None = None
        self._context_cache: ConversationContext | None = None
        self._context_hydrated = False

        # Participant tracking (simplified from ParticipantTracker)
        self._participants: list[dict[str, Any]] = []
        self._participants_loaded = False
        self._last_participants_sent: list[dict[str, Any]] | None = None

        # LLM context tracking
        self._llm_initialized = False

        # Dedupe cache (LRU for detecting duplicates during sync)
        self._processed_ids: OrderedDict[str, bool] = OrderedDict()
        self._max_processed_ids: int = 5

        # Crash recovery: sync point marker and retry tracking
        self._first_ws_msg_id: str | None = None  # First WS message = sync point
        self._retry_tracker = MessageRetryTracker(
            max_retries=self.config.max_message_retries,
            room_id=room_id,
        )
        self._sync_complete = False  # True after sync with /next completes

        # Graceful shutdown: event signaled when state becomes idle
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # Start as idle

        # Pending system messages to inject (e.g., contact broadcasts)
        self._pending_system_messages: list[str] = []

    @property
    def thread_id(self) -> str:
        """LangGraph thread_id = room_id."""
        return self.room_id

    @property
    def is_processing(self) -> bool:
        """Check if context is currently processing an event."""
        return self.state == "processing"

    def _set_state(self, new_state: Literal["starting", "idle", "processing"]) -> None:
        """
        Set the execution state and update the idle event accordingly.

        This ensures the idle event is properly synchronized with state changes
        for graceful shutdown coordination.
        """
        self.state = new_state
        if new_state == "processing":
            self._idle_event.clear()
        else:
            self._idle_event.set()

    @property
    def is_running(self) -> bool:
        """Check if context is running (task exists and not done)."""
        return (
            self._process_loop_task is not None and not self._process_loop_task.done()
        )

    @property
    def participants(self) -> list[dict[str, Any]]:
        """Get current participants list (copy)."""
        return self._participants.copy()

    @property
    def is_llm_initialized(self) -> bool:
        """Check if LLM has been initialized with system prompt."""
        return self._llm_initialized

    def mark_llm_initialized(self) -> None:
        """Mark that system prompt has been sent to LLM."""
        self._llm_initialized = True
        logger.debug("ExecutionContext %s: LLM initialized", self.room_id)

    # --- Execution protocol implementation ---

    async def start(self) -> None:
        """
        Start background processing for this room.

        Creates an asyncio task that processes events from the queue.
        """
        if self._is_running:
            logger.warning("ExecutionContext %s already running", self.room_id)
            return

        logger.info("Starting ExecutionContext for room: %s", self.room_id)
        self._is_running = True
        self._process_loop_task = asyncio.create_task(
            self._process_loop(),
            name=f"execution-{self.room_id}",
        )

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop processing with optional graceful timeout.

        If timeout is provided, waits up to that many seconds for current
        message processing to complete before cancelling. If timeout is None,
        cancels immediately via task.cancel().

        Args:
            timeout: Optional seconds to wait for current processing to complete.
                     None means cancel immediately.

        Returns:
            True if stopped gracefully (processing completed or was idle),
            False if had to cancel mid-processing after timeout.
        """
        if self._process_loop_task is None:
            return True

        logger.info("Stopping ExecutionContext for room: %s", self.room_id)

        graceful = True

        if timeout is not None and self.is_processing:
            # Wait for current processing to complete
            graceful = await self._wait_for_idle(timeout)
            if not graceful:
                logger.warning(
                    "ExecutionContext %s: Timeout waiting for processing, "
                    "cancelling mid-execution",
                    self.room_id,
                )

        # Signal stop and cancel the task
        self._is_running = False
        self._process_loop_task.cancel()
        try:
            await self._process_loop_task
        except asyncio.CancelledError:
            pass
        self._process_loop_task = None
        return graceful

    async def _wait_for_idle(self, timeout: float) -> bool:
        """
        Wait for the execution to become idle (not processing).

        Uses event-based waiting for efficient notification when processing completes.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if became idle within timeout, False if timed out.
        """
        if not self.is_processing:
            return True

        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def on_event(self, event: PlatformEvent) -> None:
        """
        Handle a platform event - add to queue.

        Called by RoomPresence/AgentRuntime when an event arrives.
        Tracks first WebSocket message ID for crash recovery sync.
        """
        # Track first WebSocket message ID for sync point
        if isinstance(event, MessageEvent) and self._first_ws_msg_id is None:
            msg_id = event.payload.id if event.payload else None
            if msg_id:
                self._first_ws_msg_id = msg_id
                logger.debug("Sync point marker set: %s", msg_id)

        self.queue.put_nowait(event)
        logger.debug("Event %s enqueued for room %s", event.type, self.room_id)

    async def request_resync(self) -> None:
        """
        Signal the process loop to re-poll /next immediately.

        Pushes a sentinel into the event queue so the Phase 2 loop wakes up
        and runs a /next catch-up without waiting for the idle timeout. Called
        by AgentRuntime after WebSocket reconnect.
        """
        self.queue.put_nowait(_ResyncRequest())  # type: ignore[arg-type]  # Sentinel is intentionally not a PlatformEvent.
        logger.debug("ExecutionContext %s: Resync sentinel enqueued", self.room_id)

    # --- Participant management ---

    def add_participant(self, participant: dict) -> bool:
        """
        Add participant (from WebSocket event).

        Returns:
            True if added, False if duplicate
        """
        if any(p.get("id") == participant.get("id") for p in self._participants):
            return False

        self._participants.append(
            {
                "id": participant.get("id"),
                "name": participant.get("name"),
                "type": participant.get("type"),
                "handle": participant.get("handle"),
            }
        )
        logger.debug(
            "ExecutionContext %s: Added participant %s",
            self.room_id,
            participant.get("name"),
        )
        return True

    def remove_participant(self, participant_id: str) -> bool:
        """
        Remove participant (from WebSocket event).

        Returns:
            True if removed, False if not found
        """
        before = len(self._participants)
        self._participants = [
            p for p in self._participants if p.get("id") != participant_id
        ]
        return len(self._participants) < before

    def participants_changed(self) -> bool:
        """Check if participants changed since last mark_participants_sent()."""
        if self._last_participants_sent is None:
            return True

        last_ids = {p.get("id") for p in self._last_participants_sent}
        current_ids = {p.get("id") for p in self._participants}
        return last_ids != current_ids

    def mark_participants_sent(self) -> None:
        """Mark current participants as sent to LLM."""
        self._last_participants_sent = self._participants.copy()

    def inject_system_message(self, message: str) -> None:
        """
        Queue a system message for injection on next processing.

        Used by ContactEventHandler to broadcast contact changes
        into all active sessions.

        Args:
            message: System message to inject
        """
        self._pending_system_messages.append(message)
        logger.debug(
            "ExecutionContext %s: Queued system message: %s",
            self.room_id,
            message[:50],
        )

    def get_pending_system_messages(self) -> list[str]:
        """
        Get and clear pending system messages.

        Returns:
            List of pending messages (cleared after call)
        """
        messages = self._pending_system_messages.copy()
        self._pending_system_messages.clear()
        return messages

    async def load_participants(self) -> list[dict[str, Any]]:
        """Load participants from API."""
        if self._participants_loaded:
            return self._participants

        try:
            response = await self.link.rest.agent_api_participants.list_agent_chat_participants(
                chat_id=self.room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            if response.data:
                self._participants = [
                    {
                        "id": p.id,
                        "name": p.name,
                        "type": p.type,
                        "handle": getattr(p, "handle", None),
                    }
                    for p in response.data
                ]
            self._participants_loaded = True
        except Exception as e:
            logger.warning(
                "Failed to load participants for room %s: %s", self.room_id, e
            )
            self._participants_loaded = True

        return self._participants

    # --- Context building ---

    async def hydrate(self) -> None:
        """
        Hydrate conversation context for this room.

        Called lazily on first event to load participant list and
        (optionally) conversation history.

        Participants are always loaded (lightweight, universally needed).
        If enable_context_hydration is False, skips history loading
        (useful for agents that manage their own state like Letta).
        """
        if self._context_hydrated:
            return

        # Always load participants (lightweight, universally needed)
        await self.load_participants()

        # Skip history hydration if disabled
        if not self.config.enable_context_hydration:
            logger.debug("History hydration disabled for room: %s", self.room_id)
            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=[],
                participants=self._participants,
                hydrated_at=datetime.now(timezone.utc),
            )
            self._context_hydrated = True
            return

        logger.debug("Hydrating context for room: %s", self.room_id)

        try:
            # Load context from API
            context_response = (
                await self.link.rest.agent_api_context.get_agent_chat_context(
                    chat_id=self.room_id,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            )

            messages = []
            if context_response.data:
                for item in context_response.data:
                    messages.append(context_item_to_dict(item))

            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=messages,
                participants=self._participants,
                hydrated_at=datetime.now(timezone.utc),
            )
            self._context_hydrated = True

            logger.debug(
                "Context hydrated: %s messages, %s participants",
                len(messages),
                len(self._participants),
            )

        except Exception as e:
            logger.warning("Context hydration failed: %s", e)
            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=[],
                participants=self._participants,
                hydrated_at=datetime.now(timezone.utc),
            )
            self._context_hydrated = True

    def _is_context_cache_expired(self) -> bool:
        """Check whether the hydrated context cache has exceeded its TTL."""
        if self._context_cache is None:
            return False

        ttl_seconds = self.config.context_cache_ttl_seconds
        if ttl_seconds <= 0:
            return True

        age_seconds = (
            datetime.now(timezone.utc) - self._context_cache.hydrated_at
        ).total_seconds()
        return age_seconds > ttl_seconds

    def _invalidate_context_cache(self) -> None:
        """Clear hydrated context so the next access refreshes it."""
        self._context_cache = None
        self._context_hydrated = False

    def _expire_context_cache_if_needed(self) -> bool:
        """Invalidate stale cached context before it can be returned."""
        if not self._is_context_cache_expired():
            return False

        logger.debug("ExecutionContext %s: Context cache expired", self.room_id)
        self._invalidate_context_cache()
        return True

    async def _ensure_fresh_context(self, *, force_refresh: bool = False) -> None:
        """Hydrate context if missing, expired, or explicitly refreshed."""
        if force_refresh:
            self._invalidate_context_cache()
        else:
            self._expire_context_cache_if_needed()

        if not self._context_hydrated:
            await self.hydrate()

    def build_context(self) -> ConversationContext:
        """
        Build context dict for LLM.

        Returns cached context or empty context if not hydrated.
        """
        self._expire_context_cache_if_needed()
        if self._context_cache:
            return self._context_cache

        return ConversationContext(
            room_id=self.room_id,
            messages=[],
            participants=self._participants,
            hydrated_at=datetime.now(timezone.utc),
        )

    async def get_context(self, force_refresh: bool = False) -> ConversationContext:
        """
        Get conversation context (lazy, cached).

        Args:
            force_refresh: Force refresh from API even if cached
        """
        await self._ensure_fresh_context(force_refresh=force_refresh)

        return self.build_context()

    def get_history_for_llm(
        self, exclude_message_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get conversation history formatted for LLM injection.

        Returns list of dicts with:
        - role: "assistant" or "user"
        - content: message content
        - sender_name: original sender name

        Args:
            exclude_message_id: Message ID to exclude (usually current message)

        Returns:
            List of message dicts ready for LLM formatting.
        """
        self._expire_context_cache_if_needed()
        if not self.config.enable_context_hydration:
            return []

        if not self._context_cache:
            return []

        # Import here to avoid circular dependency
        from thenvoi.runtime.formatters import format_history_for_llm

        return format_history_for_llm(
            self._context_cache.messages,
            exclude_id=exclude_message_id,
            participants=self._participants,
        )

    def build_participants_message(self) -> str:
        """Build a system message with current participant list for LLM."""
        from thenvoi.runtime.formatters import build_participants_message

        return build_participants_message(self._participants)

    async def _notify_participant_added(self, event: ParticipantAddedEvent) -> None:
        """Fire optional participant-added callback without breaking execution."""
        if self._on_participant_added is None:
            return

        try:
            await self._on_participant_added(self.room_id, event)
        except Exception as e:
            logger.error(
                "on_participant_added error for %s: %s",
                self.room_id,
                e,
                exc_info=True,
            )

    async def _notify_participant_removed(self, event: ParticipantRemovedEvent) -> None:
        """Fire optional participant-removed callback without breaking execution."""
        if self._on_participant_removed is None:
            return

        try:
            await self._on_participant_removed(self.room_id, event)
        except Exception as e:
            logger.error(
                "on_participant_removed error for %s: %s",
                self.room_id,
                e,
                exc_info=True,
            )

    # --- Internal processing ---

    async def _process_loop(self) -> None:
        """
        Main processing loop for this room.

        SYNCHRONIZATION FLOW:
        1. Call /next to get unprocessed messages from backend
        2. For each /next message, check if it matches WebSocket queue head
        3. If match → synchronized! Process once, then switch to WebSocket only
        4. If no match → process /next message, repeat
        5. After sync, process only from WebSocket queue

        Uses asyncio cancellation for shutdown.
        """
        try:
            # Phase 1: Sync via /next until we catch up with WebSocket
            await self._synchronize_with_next()
            self._set_state("idle")
            logger.info(
                "ExecutionContext %s: Synchronized, switching to WebSocket",
                self.room_id,
            )

            # Phase 2: Process from WebSocket queue, with idle-timeout resync safety net
            while True:
                logger.debug(
                    "ExecutionContext %s: Waiting for next event (queue size=%d)",
                    self.room_id,
                    self.queue.qsize(),
                )
                try:
                    event = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=self.config.idle_resync_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.debug(
                        "ExecutionContext %s: Idle for %ss, re-polling /next",
                        self.room_id,
                        self.config.idle_resync_seconds,
                    )
                    await self._resync_pending_messages()
                    continue

                if isinstance(event, _ResyncRequest):
                    logger.debug(
                        "ExecutionContext %s: Resync requested (post-reconnect)",
                        self.room_id,
                    )
                    await self._resync_pending_messages()
                    continue

                await self._process_event(event)

        except asyncio.CancelledError:
            logger.debug("ExecutionContext %s cancelled", self.room_id)
        except Exception as e:
            logger.exception("ExecutionContext %s error: %s", self.room_id, e)

        logger.debug("ExecutionContext %s loop exited", self.room_id)

    async def _synchronize_with_next(self) -> None:
        """
        Synchronize backlog via /next API until caught up with WebSocket.

        First recovers any messages stuck in 'processing' state from a
        previous crash, then processes pending messages via /next.

        Uses _first_ws_msg_id marker:
        1. Recover stale processing messages (crash recovery)
        2. Call /next to get next unprocessed message
        3. If None → no backlog, we're synced
        4. Check if message ID matches _first_ws_msg_id (first WebSocket message)
        5. If match → synced! Process this message, pop duplicate from queue
        6. If no match → process /next message, repeat from step 1
        """
        logger.debug(
            "ExecutionContext %s: Starting /next synchronization", self.room_id
        )

        try:
            # Recover messages stuck in 'processing' state from a previous crash.
            # The /next endpoint skips these, so we must handle them explicitly.
            await self._recover_stale_processing_messages()
            while True:  # Cancellation handles exit
                next_msg = await self._get_next_message()

                if next_msg is None:
                    logger.debug(
                        "ExecutionContext %s: /next returned None, synced",
                        self.room_id,
                    )
                    break

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    logger.warning(
                        "ExecutionContext %s: Skipping permanently failed message %s",
                        self.room_id,
                        next_msg.id,
                    )
                    break

                if next_msg.id == self._first_ws_msg_id:
                    logger.info(
                        "ExecutionContext %s: Sync point reached at message %s",
                        self.room_id,
                        next_msg.id,
                    )
                    await self._process_backlog_message(next_msg)
                    # Remove all WS copies of the sync-point message while
                    # preserving the relative order of other queued events.
                    self._drain_duplicate_from_queue(next_msg.id)

                    self._first_ws_msg_id = None  # Clear marker
                    break

                logger.debug(
                    "ExecutionContext %s: Processing backlog message %s",
                    self.room_id,
                    next_msg.id,
                )
                await self._process_backlog_message(next_msg)

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    logger.warning(
                        "ExecutionContext %s: Message %s permanently failed",
                        self.room_id,
                        next_msg.id,
                    )
                    break

        except Exception as e:
            logger.exception("ExecutionContext %s: Sync error: %s", self.room_id, e)

        logger.debug("ExecutionContext %s: Synchronization complete", self.room_id)
        self._sync_complete = True

    async def _recover_stale_processing_messages(self) -> None:
        """
        Recover messages stuck in 'processing' state from a previous crash.

        When an agent crashes mid-processing, the message stays in 'processing'
        state on the server. The /next endpoint skips these messages, so the
        agent would never pick them up again. This method finds such messages
        and re-processes them by calling mark_processing (creates a new attempt).
        """
        stale_messages = await self.link.get_stale_processing_messages(self.room_id)
        if not stale_messages:
            return

        logger.info(
            "ExecutionContext %s: Recovering %d stale processing message(s)",
            self.room_id,
            len(stale_messages),
        )

        for msg in stale_messages:
            logger.info(
                "ExecutionContext %s: Re-processing stale message %s",
                self.room_id,
                msg.id,
            )
            try:
                await self._process_backlog_message(msg)
            except Exception:
                logger.exception(
                    "ExecutionContext %s: Failed to recover stale message %s",
                    self.room_id,
                    msg.id,
                )

    async def _get_next_message(self) -> PlatformMessage | None:
        """
        Get next unprocessed message from REST API.

        Returns None if no more messages in backlog (204 No Content).
        Delegates to ThenvoiLink.get_next_message().
        """
        return await self.link.get_next_message(self.room_id)

    async def _resync_pending_messages(self) -> None:
        """
        Poll /next to catch up on messages missed while idle or disconnected.

        Runs the same REST catch-up loop as startup sync but without the
        WebSocket sync-point marker. Called:
        - After idle timeout in Phase 2 (platform may have missed a push)
        - After WebSocket reconnect (messages arrived during downtime)
        """
        logger.debug(
            "ExecutionContext %s: Re-polling /next for missed messages", self.room_id
        )
        caught_up = 0
        try:
            while True:
                next_msg = await self._get_next_message()
                if next_msg is None:
                    break

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    logger.warning(
                        "ExecutionContext %s: Skipping permanently failed message %s during resync",
                        self.room_id,
                        next_msg.id,
                    )
                    break

                logger.info(
                    "ExecutionContext %s: Catching up missed message %s via /next resync",
                    self.room_id,
                    next_msg.id,
                )
                await self._process_backlog_message(next_msg)
                caught_up += 1

                if caught_up % 100 == 0:
                    logger.info(
                        "ExecutionContext %s: Still catching up, %d messages processed so far",
                        self.room_id,
                        caught_up,
                    )

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    break

        except Exception as e:
            logger.exception(
                "ExecutionContext %s: Error during /next resync: %s",
                self.room_id,
                e,
            )

        if caught_up:
            logger.info(
                "ExecutionContext %s: Caught up %d missed message(s) via /next resync",
                self.room_id,
                caught_up,
            )
        else:
            logger.debug(
                "ExecutionContext %s: No missed messages found via /next resync",
                self.room_id,
            )

    async def _process_backlog_message(self, msg: PlatformMessage) -> None:
        """
        Process a backlog message from /next during sync.

        Full lifecycle:
        1. Check if permanently failed or duplicate
        2. Record attempt with retry tracker
        3. Mark as processing on server
        4. Execute handler
        5. Mark as processed (success) or failed (exception)
        """
        msg_id = msg.id

        # Skip messages from self (agent's own messages) to avoid infinite loops
        if (
            self._agent_id
            and msg.sender_type == "Agent"
            and msg.sender_id == self._agent_id
        ):
            logger.debug("Skipping self-message %s", msg_id)
            return

        # Skip permanently failed messages
        if self._retry_tracker.is_permanently_failed(msg_id):
            logger.debug("Skipping permanently failed message %s", msg_id)
            return

        # Skip if already processed (dedupe)
        if msg_id in self._processed_ids:
            self._processed_ids.move_to_end(msg_id)
            logger.debug("Skipping duplicate backlog message: %s", msg_id)
            return

        # Track attempts - check if exceeded BEFORE processing
        attempts, exceeded = self._retry_tracker.record_attempt(msg_id)
        if exceeded:
            logger.warning(
                "Message %s exceeded max retries (%s attempts)", msg_id, attempts
            )
            return

        self._set_state("processing")
        logger.info("Processing backlog message %s in room %s", msg_id, self.room_id)

        try:
            # Mark as processing on server BEFORE we start
            await self.link.mark_processing(self.room_id, msg_id)

            # Hydrate context on first message (loads participants always,
            # history only if enable_context_hydration is True)
            await self._ensure_fresh_context()

            # Format timestamps for MessageCreatedPayload validation
            created_at_str = (
                msg.created_at.isoformat()
                if msg.created_at
                else datetime.now(timezone.utc).isoformat()
            )

            # Normalize metadata.mentions to include username field
            metadata = msg.metadata.copy() if msg.metadata else {}
            if "mentions" in metadata:
                normalized_mentions = []
                for mention in metadata.get("mentions", []):
                    if isinstance(mention, dict):
                        normalized_mentions.append(
                            {
                                "id": mention.get("id", ""),
                                "username": mention.get("username")
                                or mention.get("name")
                                or mention.get("id", ""),
                            }
                        )
                metadata["mentions"] = normalized_mentions
            else:
                metadata["mentions"] = []

            if "status" not in metadata:
                metadata["status"] = "sent"

            # Create event from message for handler
            from thenvoi.client.streaming import MessageCreatedPayload, MessageMetadata

            event = MessageEvent(
                room_id=self.room_id,
                payload=MessageCreatedPayload(
                    id=msg.id,
                    content=msg.content,
                    sender_id=msg.sender_id,
                    sender_type=msg.sender_type,
                    message_type=msg.message_type,
                    metadata=MessageMetadata(**metadata),
                    chat_room_id=self.room_id,
                    inserted_at=created_at_str,
                    updated_at=created_at_str,
                ),
            )

            # Call execution handler
            await self._on_execute(self, event)

            # SUCCESS: Mark as processed on server
            await self.link.mark_processed(self.room_id, msg_id)
            self._retry_tracker.mark_success(msg_id)

            # Track in dedupe cache
            self._processed_ids[msg_id] = True
            if len(self._processed_ids) > self._max_processed_ids:
                self._processed_ids.popitem(last=False)

            logger.debug("Message %s processed successfully", msg_id)

        except Exception as e:
            # FAILURE: Mark as failed on server
            logger.error(
                "Error processing backlog message %s: %s", msg_id, e, exc_info=True
            )
            await self.link.mark_failed(self.room_id, msg_id, _error_label(e))

        finally:
            self._set_state("idle")

    def _drain_duplicate_from_queue(self, msg_id: str) -> None:
        """
        Remove duplicate message from WebSocket queue after sync point reached.

        The message at sync point exists in both /next and WS queue.
        """
        # Drain queue and re-add non-duplicates
        items = []
        while not self.queue.empty():
            try:
                event = self.queue.get_nowait()
                if (
                    isinstance(event, MessageEvent)
                    and event.payload
                    and event.payload.id == msg_id
                ):
                    logger.debug("Removed duplicate from WS queue: %s", msg_id)
                    continue
                items.append(event)
            except asyncio.QueueEmpty:
                break

        # Re-add non-duplicates
        for item in items:
            self.queue.put_nowait(item)

    async def _process_event(self, event: PlatformEvent) -> None:
        """
        Process single event through execution callback.

        For message events, handles full lifecycle:
        1. Check if permanently failed or duplicate
        2. Record attempt with retry tracker
        3. Mark as processing on server
        4. Execute handler
        5. Mark as processed (success) or failed (exception)
        """
        payload = event.payload if isinstance(event, MessageEvent) else None
        msg_id = payload.id if payload else None

        # For messages: check if we should skip
        if isinstance(event, MessageEvent) and msg_id and payload:
            # Skip messages from self (agent's own messages) to avoid infinite loops
            if (
                self._agent_id
                and payload.sender_type == "Agent"
                and payload.sender_id == self._agent_id
            ):
                logger.debug("Skipping self-message %s", msg_id)
                return

            # Detect synthetic messages (e.g., contact events injected into hub room)
            # These don't exist in the database, so skip all tracking and marking
            is_synthetic = (
                payload.sender_type == SYNTHETIC_SENDER_TYPE
                and payload.sender_id == SYNTHETIC_CONTACT_EVENTS_SENDER_ID
            )
            if is_synthetic:
                logger.debug("Processing synthetic contact event message")
                msg_id = None  # Clear to skip message marking later
                # Skip all tracking for synthetic messages - go directly to processing
            else:
                # Only track retries and duplicates for real messages
                # Skip permanently failed messages
                if self._retry_tracker.is_permanently_failed(msg_id):
                    logger.debug("Skipping permanently failed message %s", msg_id)
                    return

                # Skip duplicates
                if msg_id in self._processed_ids:
                    self._processed_ids.move_to_end(msg_id)
                    logger.debug("Skipping duplicate message %s", msg_id)
                    return

                # Track attempts
                attempts, exceeded = self._retry_tracker.record_attempt(msg_id)
                if exceeded:
                    logger.warning(
                        "Message %s exceeded max retries (%s attempts)",
                        msg_id,
                        attempts,
                    )
                    return

        self._set_state("processing")
        logger.debug("Processing %s in room %s", event.type, self.room_id)

        try:
            # For messages: mark as processing on server
            if isinstance(event, MessageEvent) and msg_id:
                await self.link.mark_processing(self.room_id, msg_id)

            # Hydrate context on first event (loads participants always,
            # history only if enable_context_hydration is True)
            await self._ensure_fresh_context()

            # Handle participant events internally
            if isinstance(event, ParticipantAddedEvent) and event.payload:
                self.add_participant(event.payload.model_dump())
                await self._notify_participant_added(event)
            elif isinstance(event, ParticipantRemovedEvent) and event.payload:
                self.remove_participant(event.payload.id)
                await self._notify_participant_removed(event)

            # Call execution handler
            await self._on_execute(self, event)

            # For messages: mark as processed on server
            if isinstance(event, MessageEvent) and msg_id:
                await self.link.mark_processed(self.room_id, msg_id)
                self._retry_tracker.mark_success(msg_id)

                # Track in dedupe cache
                self._processed_ids[msg_id] = True
                if len(self._processed_ids) > self._max_processed_ids:
                    self._processed_ids.popitem(last=False)

            logger.debug("Event %s processed successfully", event.type)

        except Exception as e:
            logger.exception("Error processing %s: %s", event.type, e)
            # For messages: mark as failed on server
            if isinstance(event, MessageEvent) and msg_id:
                await self.link.mark_failed(self.room_id, msg_id, _error_label(e))

        finally:
            self._set_state("idle")
