"""Band bridge: WS subscriber + event forwarder. No Band logic.

The bridge holds N Band Phoenix WS connections (one per agent identity
in :class:`BridgeConfig.agents`) and forwards every received event as a
JSON payload to the agent's configured target. All semantic work — mention
parsing, message construction, lifecycle marking, participant resolution —
lives in the agent's container, which runs the Band SDK.

Architecture:
    BandBridge
        ├── AgentRunner(agent_1)  → BandLink(agent_1) → Forwarder(target_1)
        ├── AgentRunner(agent_2)  → BandLink(agent_2) → Forwarder(target_2)
        └── AgentRunner(agent_3)  → BandLink(agent_3) → Forwarder(target_3)
    + HealthServer (shared, reports per-agent connection status)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.client.streaming import MessageCreatedPayload
from band.runtime.types import PlatformMessage
from band.platform.event import (
    ContactAddedEvent,
    ContactRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
)
from band.platform.link import BandLink

from .config import AgentConfig, BridgeConfig, ReconnectConfig
from .control import ControlSignalHandler
from .forwarder import Forwarder, build_forwarder
from .health import HealthServer

logger = logging.getLogger(__name__)

_DEDUP_MAX_SIZE = 10_000

_FORWARDABLE_EVENTS: tuple[type, ...] = (
    MessageEvent,
    RoomAddedEvent,
    RoomRemovedEvent,
    RoomDeletedEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)


class AgentRunner:
    """One agent identity: WS link, dedup, reconnect, event forwarding.

    Each runner subscribes as a single Band agent and forwards every
    received event to its configured forwarder. No mention parsing, no
    AgentTools, no lifecycle marking — those belong in the SDK that runs
    inside the agent's container.
    """

    def __init__(
        self,
        agent_config: AgentConfig,
        ws_url: str,
        rest_url: str,
        forwarder: Forwarder,
        reconnect: ReconnectConfig,
        shutdown_event: asyncio.Event,
        link: BandLink | None = None,
        max_concurrent_forwards: int = 32,
    ) -> None:
        """Initialize the runner.

        Args:
            agent_config: Per-agent identity and target.
            ws_url: Band WS URL.
            rest_url: Band REST URL.
            forwarder: Where to send received events.
            reconnect: Backoff config for reconnect loop.
            shutdown_event: Shared shutdown signal (cancels the loop).
            link: Pre-built BandLink (test injection). If None, one is
                constructed from agent_config and the URLs.
            max_concurrent_forwards: Cap on concurrent in-flight forwards for
                this agent. Tasks are still cheaply created per event; only
                this many can be inside the forward path at once.
        """
        self._config = agent_config
        self._forwarder = forwarder
        self._reconnect = reconnect
        self._shutdown_event = shutdown_event
        self._connected_event = asyncio.Event()
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        # In-flight forward tasks per room, so a control signal (interrupt/stop)
        # can cancel whatever forwards are currently live for a room. Tracked as
        # a set because same-room events serialize on the per-room lock: while
        # one task forwards, others wait on that lock, and all of them must be
        # cancellable — otherwise interrupt cancels a queued waiter while the
        # running forward keeps going. Populated by _safe_handle_event at the
        # top of processing, discarded in its finally.
        self._active_room_tasks: dict[str, set[asyncio.Task[None]]] = {}
        # Control-signal handling (interrupt/stop/play) lives in its own type;
        # it delegates cancel/nudge/room-list back to this runner but owns the
        # correlation-id dedup and routing (see control.py).
        self._control = ControlSignalHandler(self)
        # Background tasks spawned by control signals (play nudges) so the WS
        # receive task isn't blocked awaiting them. Tracked here to be
        # cancelled on close() and to keep a strong ref (create_task alone
        # doesn't).
        self._control_tasks: set[asyncio.Task[None]] = set()
        # Per-room locks: events for the same room are forwarded sequentially
        # so the container always sees a settled history (its own prior reply
        # is already posted before the next invocation reads room context).
        # Different rooms are unaffected — they forward in parallel.
        self._room_locks: dict[str, asyncio.Lock] = {}
        # Back-pressure on forward fan-out: a burst of WS events (or a slow
        # target) would otherwise pile up unbounded tasks and fan an equal
        # number of HTTP/AgentCore calls at the backend.
        self._forward_semaphore = asyncio.Semaphore(max_concurrent_forwards)
        self._link = link or BandLink(
            agent_id=agent_config.agent_id,
            api_key=agent_config.api_key,
            ws_url=ws_url,
            rest_url=rest_url,
        )

    @property
    def agent_id(self) -> str:
        return self._config.agent_id

    @property
    def is_connected(self) -> bool:
        return self._link.is_connected

    @property
    def link(self) -> BandLink:
        return self._link

    @property
    def forwarder(self) -> Forwarder:
        return self._forwarder

    @property
    def _shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    async def run(self) -> None:
        """Run the consume loop with exponential-backoff reconnect."""
        delay = self._reconnect.initial_delay
        attempts = 0

        while not self._shutting_down:
            self._connected_event.clear()
            try:
                await self._connect_and_consume()
                break  # Clean exit
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._shutting_down:
                    break

                # Runtime disconnect (was connected, now lost) — reset backoff.
                if self._connected_event.is_set():
                    delay = self._reconnect.initial_delay
                    attempts = 0

                attempts += 1
                if (
                    self._reconnect.max_retries > 0
                    and attempts >= self._reconnect.max_retries
                ):
                    logger.error(
                        "Agent %s: max reconnect attempts (%d) reached, giving up",
                        self._config.agent_id,
                        self._reconnect.max_retries,
                    )
                    break

                logger.warning(
                    "Agent %s: connection lost, reconnecting in %.1fs",
                    self._config.agent_id,
                    delay,
                    exc_info=True,
                )

                try:
                    await self._link.disconnect()
                except Exception:
                    logger.debug(
                        "Agent %s: error during disconnect cleanup",
                        self._config.agent_id,
                        exc_info=True,
                    )

                await asyncio.sleep(self._backoff_sleep_seconds(delay))

                delay = min(
                    delay * self._reconnect.multiplier,
                    self._reconnect.max_delay,
                )

        logger.info("Agent %s: reconnect loop exited", self._config.agent_id)

    def _backoff_sleep_seconds(self, delay: float) -> float:
        """Compute the next backoff sleep with partial jitter.

        ``ReconnectConfig.jitter`` is the *fraction* of the delay that
        randomizes — 0 means a fixed ``delay``, 1 means full jitter uniform on
        ``[0, delay]``, and 0.25 means 75% of the delay is fixed with the
        remaining 25% randomized. Treating it as a boolean (the previous
        ``jitter > 0`` check) collapsed 0.25/0.5/1.0 to the same full-jitter
        behavior.
        """
        jitter = min(self._reconnect.jitter, 1.0)
        return delay * (1 - jitter) + random.uniform(0, delay * jitter)  # noqa: S311

    async def close(self) -> None:
        """Disconnect link and close forwarder. Idempotent."""
        for task in tuple(self._control_tasks):
            task.cancel()
        try:
            await self._link.disconnect()
        except Exception:
            logger.warning(
                "Agent %s: error during link disconnect",
                self._config.agent_id,
                exc_info=True,
            )
        try:
            await self._forwarder.close()
        except Exception:
            logger.warning(
                "Agent %s: error during forwarder close",
                self._config.agent_id,
                exc_info=True,
            )

    async def _connect_and_consume(self) -> None:
        """Connect, subscribe, and consume events until shutdown."""
        await self._link.connect()
        self._connected_event.set()

        # Route control signals (interrupt/stop/play) to the control handler.
        # Set as soon as connected so the hook is live for the agent_control
        # channel BandLink.connect() already joined.
        self._link.on_control = self._control.handle

        await self._link.subscribe_agent_rooms(self._config.agent_id)

        existing_rooms = await self._fetch_existing_rooms()
        if existing_rooms:
            await asyncio.gather(
                *[self._link.subscribe_room(rid) for rid in existing_rooms]
            )
            logger.info(
                "Agent %s: subscribed to %d existing rooms",
                self._config.agent_id,
                len(existing_rooms),
            )
            # Rehydrate: Phoenix only pushes events from subscription time
            # forward, so messages that arrived (or got stuck mid-processing)
            # while the bridge was down are never redelivered on the WS. Nudge
            # the container once per room with the oldest unprocessed message.
            await self._rehydrate_backlog(existing_rooms)

        logger.info(
            "Agent %s: connected and listening for events",
            self._config.agent_id,
        )

        shutdown_fut = asyncio.ensure_future(self._shutdown_event.wait())
        active_tasks: set[asyncio.Task[None]] = set()
        next_fut: asyncio.Future[object] | None = None
        try:
            while True:
                next_fut = asyncio.ensure_future(anext(self._link))
                done, _ = await asyncio.wait(
                    {shutdown_fut, next_fut},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if shutdown_fut in done:
                    next_fut.cancel()
                    break
                try:
                    event = next_fut.result()
                except StopAsyncIteration:
                    break
                except RuntimeError as e:
                    # PEP 479: StopAsyncIteration raised inside an async
                    # generator surfaces as RuntimeError on the caller side.
                    if isinstance(e.__cause__, StopAsyncIteration) or isinstance(
                        e.__context__, StopAsyncIteration
                    ):
                        break
                    raise
                next_fut = None

                # Fire forward as a background task so we keep pulling events.
                task = asyncio.create_task(self._safe_handle_event(event))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
        finally:
            if not shutdown_fut.done():
                shutdown_fut.cancel()
            if next_fut is not None and not next_fut.done():
                next_fut.cancel()
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

    async def _fetch_existing_rooms(self) -> list[str]:
        try:
            response = await self._link.rest.agent_api_chats.list_agent_chats(
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            if response.data:
                return [room.id for room in response.data]
        except Exception:
            logger.warning(
                "Agent %s: failed to fetch existing rooms",
                self._config.agent_id,
                exc_info=True,
            )
        return []

    async def _rehydrate_backlog(self, room_ids: list[str]) -> None:
        """Forward one nudge per room that has an unanswered message.

        For each room we ask the platform for the oldest actionable message
        (``/next``) and, if one exists, forward it as a synthetic
        ``message_created`` event through the normal handle path (dedup +
        per-room lock + forwarder). The platform's ``/next`` returns any
        message not yet in ``processed`` state — including ones stuck in
        ``processing`` from a crashed container — so a single ``/next`` call
        both replays missed backlog and reclaims stuck work (``msg_id`` matches
        the container's own ``/next`` claim step).

        We forward at most ONE message per room: the bridge does no lifecycle
        marking, so ``/next`` would return the same message until the container
        marks it processed. The container's own drain loop pulls the rest.
        """
        # Rooms are independent; nudge concurrently. Per-room locks and dedup
        # still serialize each nudge against any live event for that room.
        await asyncio.gather(*(self._nudge_room(rid) for rid in room_ids))

    async def _nudge_room(self, room_id: str) -> None:
        """Forward one backlog message for a room, if ``/next`` has one.

        Shared by startup rehydration (:meth:`_rehydrate_backlog`) and a live
        ``play`` control signal (``ControlSignalHandler.handle``) — both cases
        are the same "catch up this room via /next" operation.
        """
        try:
            msg = await self._link.get_next_message(room_id)
        except Exception:
            logger.warning(
                "Agent %s: rehydration /next failed for room %s",
                self._config.agent_id,
                room_id,
                exc_info=True,
            )
            return
        if msg is None:
            return
        logger.info(
            "Agent %s: rehydrating room %s with backlog message %s",
            self._config.agent_id,
            room_id,
            msg.id,
        )
        await self._safe_handle_event(self._backlog_event(msg))

    def _backlog_event(self, msg: PlatformMessage) -> MessageEvent:
        """Wrap a ``/next`` PlatformMessage as a synthetic message_created event.

        Shaped so ``_serialize_event`` produces the same payload a live
        ``message_created`` event would — the container can't tell the two
        apart.
        """
        created_at = msg.created_at.isoformat()
        return MessageEvent(
            room_id=msg.room_id,
            payload=MessageCreatedPayload(
                id=msg.id,
                content=msg.content,
                message_type=msg.message_type,
                metadata=msg.metadata or None,
                sender_id=msg.sender_id,
                sender_type=msg.sender_type,
                sender_name=msg.sender_name or None,
                chat_room_id=msg.room_id,
                inserted_at=created_at,
                updated_at=created_at,
            ),
        )

    async def _safe_handle_event(self, event: object) -> None:
        # Track this task among the in-flight forwards for its room so a control
        # signal (interrupt/stop) can cancel it — whether it's the one holding
        # the per-room lock and forwarding, or a same-room task still waiting on
        # that lock. Room-less events (e.g. contact events) aren't tracked —
        # there's nothing room-scoped to cancel for them.
        room_id = getattr(event, "room_id", None)
        task = asyncio.current_task()
        if room_id and task is not None:
            self._active_room_tasks.setdefault(room_id, set()).add(task)
        try:
            # Semaphore caps concurrent in-flight forwards per agent. Holding
            # it across ``_handle_event`` (which includes the per-room lock +
            # the forwarder call) is the point — bursts wait here instead of
            # stacking up inside the forwarder.
            async with self._forward_semaphore:
                await self._handle_event(event)
        except Exception:
            logger.warning(
                "Agent %s: error handling event %s",
                self._config.agent_id,
                type(event).__name__,
                exc_info=True,
            )
        finally:
            if room_id:
                tasks = self._active_room_tasks.get(room_id)
                if tasks is not None:
                    tasks.discard(task)
                    if not tasks:
                        self._active_room_tasks.pop(room_id, None)

    async def _handle_event(self, event: object) -> None:
        """Manage room subscriptions; forward forwardable events.

        Subscription management for room_added / room_removed / room_deleted
        is WS plumbing — we need to subscribe/unsubscribe at the Phoenix
        channel level. The event itself is also forwarded to the agent.
        """
        if isinstance(event, RoomAddedEvent) and event.room_id:
            logger.info("Agent %s: room added %s", self._config.agent_id, event.room_id)
            try:
                await self._link.subscribe_room(event.room_id)
            except Exception:
                logger.warning(
                    "Agent %s: failed to subscribe to room %s",
                    self._config.agent_id,
                    event.room_id,
                    exc_info=True,
                )
        elif isinstance(event, (RoomRemovedEvent, RoomDeletedEvent)) and event.room_id:
            logger.info(
                "Agent %s: room removed/deleted %s",
                self._config.agent_id,
                event.room_id,
            )
            try:
                await self._link.unsubscribe_room(event.room_id)
            except Exception:
                logger.warning(
                    "Agent %s: failed to unsubscribe from room %s",
                    self._config.agent_id,
                    event.room_id,
                    exc_info=True,
                )

        # Dedup message events by message id (reconnect may redeliver).
        # Check only — recording the id happens after a successful forward so
        # that a failed forward stays retryable through WS redelivery or
        # ``_rehydrate_backlog``.
        msg_id: str | None = None
        if isinstance(event, MessageEvent) and event.payload is not None:
            msg_id = getattr(event.payload, "id", None)
            if msg_id and msg_id in self._processed_message_ids:
                logger.debug(
                    "Agent %s: skipping duplicate message %s",
                    self._config.agent_id,
                    msg_id,
                )
                return

        payload = self._serialize_event(event)
        if payload is None:
            logger.debug(
                "Agent %s: not forwarding event %s (not in forwardable set)",
                self._config.agent_id,
                type(event).__name__,
            )
            return

        # Serialize forwards per room so events from the same room don't race
        # the container's history fetch. Without this, two messages arriving
        # in quick succession invoke the container twice in parallel; both
        # invocations fetch room context before either has posted its reply,
        # both LLMs see un-answered mentions, and both respond — leading to
        # duplicate messages in the room.
        room_id = payload.get("room_id")
        room_lock = self._lock_for_room(room_id)
        forward_succeeded = False
        async with room_lock:
            try:
                await self._forwarder.forward(payload)
                forward_succeeded = True
            except Exception:
                logger.warning(
                    "Agent %s: forward failed for event %s",
                    self._config.agent_id,
                    type(event).__name__,
                    exc_info=True,
                )

        # Only remember the message id once the forward actually succeeded;
        # otherwise a transient forwarder failure would mask the message on the
        # next WS redelivery or rehydration sweep.
        if msg_id and forward_succeeded:
            self._remember_processed_message(msg_id)

        # Evict the per-room lock once the room is gone so ``_room_locks`` does
        # not grow unbounded over a long-lived bridge that cycles through many
        # ephemeral rooms. Done after the forward above releases the lock; a
        # later straggler event for the same room just creates a fresh one.
        if isinstance(event, (RoomRemovedEvent, RoomDeletedEvent)) and room_id:
            self._room_locks.pop(room_id, None)

    def _lock_for_room(self, room_id: str | None) -> asyncio.Lock:
        """Return the asyncio.Lock for a room id, creating one on first use.

        Events without a room_id (e.g. contact events) share a single
        ``_global`` lock — they're rare and not in the demo path; keeping
        them serial is the safe default.
        """
        key = room_id or "_global"
        lock = self._room_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[key] = lock
        return lock

    def _serialize_event(self, event: object) -> dict[str, Any] | None:
        """Convert a typed PlatformEvent into a JSON payload for forwarding.

        Returns None for event types we don't forward.
        """
        if not isinstance(event, _FORWARDABLE_EVENTS):
            return None

        payload_dict: dict[str, Any] | None = None
        event_payload = getattr(event, "payload", None)
        if event_payload is not None and hasattr(event_payload, "model_dump"):
            payload_dict = event_payload.model_dump(mode="json")

        return {
            "event_type": getattr(event, "type", type(event).__name__),
            "agent_id": self._config.agent_id,
            "room_id": getattr(event, "room_id", None),
            "payload": payload_dict,
            "raw": getattr(event, "raw", None),
            "forwarded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _remember_processed_message(self, message_id: str) -> None:
        """Record a message id in the bounded dedup cache."""
        if message_id in self._processed_message_ids:
            return
        self._processed_message_ids[message_id] = None
        if len(self._processed_message_ids) > _DEDUP_MAX_SIZE:
            self._processed_message_ids.popitem(last=False)

    # --- Control signals (interrupt/stop/play) ---
    #
    # Signal routing/dedup lives in ControlSignalHandler (control.py); it calls
    # back into the operations below, which touch this runner's forwarding
    # state (the per-room task registry) and the /next nudge path.

    def _spawn_play_nudge(self, room_ids: list[str]) -> None:
        """Fire ``play`` /next nudges as tracked background tasks.

        Runs off the WS receive task so a following interrupt/stop isn't queued
        behind the nudge's /next + forward. Each spawned forward still lands in
        ``_active_room_tasks`` (via ``_safe_handle_event``), so a later
        interrupt/stop can cancel it. Tasks are tracked in ``_control_tasks``
        and cancelled on ``close()``.
        """
        for room_id in room_ids:
            task = asyncio.create_task(self._nudge_room(room_id))
            self._control_tasks.add(task)
            task.add_done_callback(self._control_tasks.discard)

    def _cancel_active_forward(self, room_id: str) -> None:
        """Cancel all in-flight forward tasks for a room, if any.

        Cancels both the task currently forwarding and any same-room tasks
        still queued on the per-room lock, so a signal can't leave a waiter
        behind to run the moment the cancelled forward releases the lock.

        Best-effort: this stops the bridge from waiting and releases the room
        lock immediately. It does not guarantee the remote invocation itself
        stops — ``AgentCoreForwarder``'s underlying boto3 call runs in a
        thread and isn't killed by cancelling the awaiting task (see
        ``forwarder.py``); that is a pre-existing, documented limitation of
        that transport, not something this signal can fix.
        """
        tasks = self._active_room_tasks.get(room_id)
        if not tasks:
            return
        cancelled = 0
        for task in tuple(tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        if cancelled:
            logger.info(
                "Agent %s: cancelled %d in-flight forward(s) for room %s",
                self._config.agent_id,
                cancelled,
                room_id,
            )


class BandBridge:
    """Bridge orchestrator: N AgentRunners + signal handling + health server."""

    def __init__(
        self,
        config: BridgeConfig,
        reconnect_config: ReconnectConfig | None = None,
        forwarders: dict[str, Forwarder] | None = None,
        links: dict[str, BandLink] | None = None,
    ) -> None:
        """Initialize the bridge.

        Args:
            config: Bridge configuration.
            reconnect_config: Optional reconnect backoff (shared by runners).
            forwarders: Optional pre-built forwarders keyed by agent_id, for
                tests. If omitted, forwarders are built from each agent's
                target.
            links: Optional pre-built links keyed by agent_id, for tests.
        """
        self._config = config
        self._reconnect = reconnect_config or ReconnectConfig()
        self._shutdown_event = asyncio.Event()

        # Build forwarders
        self._forwarders: dict[str, Forwarder] = {}
        for agent in config.agents:
            if forwarders and agent.agent_id in forwarders:
                self._forwarders[agent.agent_id] = forwarders[agent.agent_id]
            else:
                self._forwarders[agent.agent_id] = build_forwarder(agent.target)

        # Build runners
        self._runners: list[AgentRunner] = []
        for agent in config.agents:
            injected_link = links.get(agent.agent_id) if links else None
            runner = AgentRunner(
                agent_config=agent,
                ws_url=config.ws_url,
                rest_url=config.rest_url,
                forwarder=self._forwarders[agent.agent_id],
                reconnect=self._reconnect,
                shutdown_event=self._shutdown_event,
                link=injected_link,
                max_concurrent_forwards=config.max_concurrent_forwards,
            )
            self._runners.append(runner)

        # Health server (reports per-runner status)
        self._health = HealthServer(
            runners=self._runners,
            port=config.health_port,
            host=config.health_host,
        )

    @property
    def runners(self) -> list[AgentRunner]:
        return list(self._runners)

    @property
    def _shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    async def run(self) -> None:
        """Start health server and all runners; run until shutdown."""
        loop = asyncio.get_running_loop()

        # Unix-only; on Windows fall back to no-op.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                logger.debug(
                    "Signal handler for %s not supported on this platform",
                    sig.name,
                )

        if not self._runners:
            logger.warning("Bridge starting with no agents configured")

        logger.info(
            "Starting bridge with %d agent(s): %s",
            len(self._runners),
            ", ".join(r.agent_id for r in self._runners),
        )

        try:
            await self._health.start()
            await asyncio.gather(*[runner.run() for runner in self._runners])
        finally:
            await self._shutdown()

    def _request_shutdown(self) -> None:
        if not self._shutdown_event.is_set():
            logger.info("Shutdown requested")
            self._shutdown_event.set()

    async def _shutdown(self) -> None:
        logger.info("Shutting down bridge...")
        await asyncio.gather(
            *[runner.close() for runner in self._runners],
            return_exceptions=True,
        )
        await self._health.stop()
        logger.info("Bridge shutdown complete")


async def main(
    config: BridgeConfig | None = None,
    reconnect: ReconnectConfig | None = None,
) -> None:
    """Bridge entry point. Loads config from env if not provided.

    Usage::

        import asyncio
        from bridge_core.bridge import main

        asyncio.run(main())
    """
    from dotenv import load_dotenv

    load_dotenv()

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if config is None:
        try:
            config = BridgeConfig.from_env()
        except ValueError:
            logger.exception("Bridge configuration error")
            raise

    bridge = BandBridge(config=config, reconnect_config=reconnect)
    await bridge.run()
