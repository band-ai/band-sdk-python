"""Thenvoi bridge: WS subscriber + event forwarder. No Band logic.

The bridge holds N Thenvoi Phoenix WS connections (one per agent identity
in :class:`BridgeConfig.agents`) and forwards every received event as a
JSON payload to the agent's configured target. All semantic work — mention
parsing, message construction, lifecycle marking, participant resolution —
lives in the agent's container, which runs the Thenvoi SDK.

Architecture:
    ThenvoiBridge
        ├── AgentRunner(agent_1)  → ThenvoiLink(agent_1) → Forwarder(target_1)
        ├── AgentRunner(agent_2)  → ThenvoiLink(agent_2) → Forwarder(target_2)
        └── AgentRunner(agent_3)  → ThenvoiLink(agent_3) → Forwarder(target_3)
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
from typing import TYPE_CHECKING, Any

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.platform.event import (
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
from thenvoi.platform.link import ThenvoiLink

from .config import AgentConfig, BridgeConfig, ReconnectConfig
from .forwarder import Forwarder, build_forwarder
from .health import HealthServer

if TYPE_CHECKING:
    pass

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

    Each runner subscribes as a single Thenvoi agent and forwards every
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
        link: ThenvoiLink | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            agent_config: Per-agent identity and target.
            ws_url: Thenvoi WS URL.
            rest_url: Thenvoi REST URL.
            forwarder: Where to send received events.
            reconnect: Backoff config for reconnect loop.
            shutdown_event: Shared shutdown signal (cancels the loop).
            link: Pre-built ThenvoiLink (test injection). If None, one is
                constructed from agent_config and the URLs.
        """
        self._config = agent_config
        self._forwarder = forwarder
        self._reconnect = reconnect
        self._shutdown_event = shutdown_event
        self._connected_event = asyncio.Event()
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._link = link or ThenvoiLink(
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
    def link(self) -> ThenvoiLink:
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

                # Full-jitter backoff.
                if self._reconnect.jitter > 0:
                    sleep_time = random.uniform(0, delay)  # noqa: S311
                else:
                    sleep_time = delay
                await asyncio.sleep(sleep_time)

                delay = min(
                    delay * self._reconnect.multiplier,
                    self._reconnect.max_delay,
                )

        logger.info("Agent %s: reconnect loop exited", self._config.agent_id)

    async def close(self) -> None:
        """Disconnect link and close forwarder. Idempotent."""
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

    async def _safe_handle_event(self, event: object) -> None:
        try:
            await self._handle_event(event)
        except Exception:
            logger.warning(
                "Agent %s: error handling event %s",
                self._config.agent_id,
                type(event).__name__,
                exc_info=True,
            )

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
        if isinstance(event, MessageEvent) and event.payload is not None:
            msg_id = getattr(event.payload, "id", None)
            if msg_id and self._is_duplicate(msg_id):
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

        try:
            await self._forwarder.forward(payload)
        except Exception:
            logger.warning(
                "Agent %s: forward failed for event %s",
                self._config.agent_id,
                type(event).__name__,
                exc_info=True,
            )

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

    def _is_duplicate(self, message_id: str) -> bool:
        """Bounded dedup cache for reconnect-redelivery."""
        if message_id in self._processed_message_ids:
            return True
        self._processed_message_ids[message_id] = None
        if len(self._processed_message_ids) > _DEDUP_MAX_SIZE:
            self._processed_message_ids.popitem(last=False)
        return False


class ThenvoiBridge:
    """Bridge orchestrator: N AgentRunners + signal handling + health server."""

    def __init__(
        self,
        config: BridgeConfig,
        reconnect_config: ReconnectConfig | None = None,
        forwarders: dict[str, Forwarder] | None = None,
        links: dict[str, ThenvoiLink] | None = None,
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

    bridge = ThenvoiBridge(config=config, reconnect_config=reconnect)
    await bridge.run()
