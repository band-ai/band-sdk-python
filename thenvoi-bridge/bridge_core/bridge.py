"""Main bridge orchestrator — connects to platform, routes @mentions to handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, TypedDict

from pydantic import BaseModel, Field, field_validator

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    RoomAddedEvent,
    RoomRemovedEvent,
)
from thenvoi.platform.link import ThenvoiLink
from thenvoi.runtime.tools import AgentTools

from .health import HealthServer
from .router import MentionRouter
from .session import InMemorySessionStore

if TYPE_CHECKING:
    from thenvoi.client.streaming import MessageCreatedPayload

    from .handler import Handler

logger = logging.getLogger(__name__)


class ParticipantRecord(TypedDict):
    """Typed dict for participant cache entries."""

    id: str
    name: str
    type: str
    handle: str | None


class BridgeConfig(BaseModel):
    """Bridge configuration loaded from environment variables."""

    agent_id: str
    api_key: str = Field(repr=False)
    ws_url: str = "wss://app.band.ai/api/v1/socket/websocket"
    rest_url: str = "https://app.band.ai"
    agent_mapping: str
    health_port: int = 8080
    health_host: str = "0.0.0.0"
    session_ttl: float = 86400.0  # 24 hours; 0 disables eviction
    handler_timeout: float = 300.0  # 5 minutes; 0 disables timeout

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        """Validate agent_id is non-empty."""
        if not v.strip():
            raise ValueError("THENVOI_AGENT_ID is required and cannot be empty")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate api_key is non-empty."""
        if not v.strip():
            raise ValueError("THENVOI_API_KEY is required and cannot be empty")
        return v

    @field_validator("agent_mapping")
    @classmethod
    def validate_agent_mapping(cls, v: str) -> str:
        """Validate agent_mapping is non-empty."""
        if not v.strip():
            raise ValueError("AGENT_MAPPING is required and cannot be empty")
        return v

    @field_validator("health_port")
    @classmethod
    def validate_health_port(cls, v: int) -> int:
        """Validate health_port is in valid TCP port range."""
        if not (1 <= v <= 65535):
            raise ValueError(f"HEALTH_PORT must be between 1 and 65535, got: {v}")
        return v

    @field_validator("session_ttl")
    @classmethod
    def validate_session_ttl(cls, v: float) -> float:
        """Validate session_ttl is non-negative (0 disables eviction)."""
        if v < 0:
            raise ValueError(f"SESSION_TTL must be non-negative, got: {v}")
        return v

    @field_validator("handler_timeout")
    @classmethod
    def validate_handler_timeout(cls, v: float) -> float:
        """Validate handler_timeout is non-negative (0 disables timeout)."""
        if v < 0:
            raise ValueError(f"HANDLER_TIMEOUT must be non-negative, got: {v}")
        return v

    @classmethod
    def from_env(cls) -> BridgeConfig:
        """Load configuration from environment variables.

        Only env vars that are explicitly set are passed to the constructor;
        unset vars fall through to model field defaults.

        Returns:
            BridgeConfig instance.

        Raises:
            ValueError: If required environment variables are missing or invalid.
        """
        required_vars = {
            "agent_id": "THENVOI_AGENT_ID",
            "api_key": "THENVOI_API_KEY",
            "agent_mapping": "AGENT_MAPPING",
        }
        missing = [
            env_var for env_var in required_vars.values() if env_var not in os.environ
        ]
        if missing:
            raise ValueError(
                f"Required environment variable(s) not set: {', '.join(missing)}"
            )

        kwargs: dict[str, Any] = {
            field: os.environ[env_var] for field, env_var in required_vars.items()
        }

        if "THENVOI_WS_URL" in os.environ:
            kwargs["ws_url"] = os.environ["THENVOI_WS_URL"]
        if "THENVOI_REST_URL" in os.environ:
            kwargs["rest_url"] = os.environ["THENVOI_REST_URL"]
        if "HEALTH_HOST" in os.environ:
            kwargs["health_host"] = os.environ["HEALTH_HOST"]

        if "HEALTH_PORT" in os.environ:
            health_port_str = os.environ["HEALTH_PORT"]
            try:
                kwargs["health_port"] = int(health_port_str)
            except ValueError:
                raise ValueError(
                    f"HEALTH_PORT must be a valid integer, got: '{health_port_str}'"
                ) from None

        if "SESSION_TTL" in os.environ:
            session_ttl_str = os.environ["SESSION_TTL"]
            try:
                kwargs["session_ttl"] = float(session_ttl_str)
            except ValueError:
                raise ValueError(
                    f"SESSION_TTL must be a valid number, got: '{session_ttl_str}'"
                ) from None

        if "HANDLER_TIMEOUT" in os.environ:
            handler_timeout_str = os.environ["HANDLER_TIMEOUT"]
            try:
                kwargs["handler_timeout"] = float(handler_timeout_str)
            except ValueError:
                raise ValueError(
                    f"HANDLER_TIMEOUT must be a valid number, got: '{handler_timeout_str}'"
                ) from None

        return cls(**kwargs)


class ReconnectConfig(BaseModel):
    """Reconnection backoff configuration."""

    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.5
    max_retries: int = 0  # 0 = unlimited

    @field_validator("initial_delay")
    @classmethod
    def validate_initial_delay(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"initial_delay must be positive, got: {v}")
        return v

    @field_validator("max_delay")
    @classmethod
    def validate_max_delay(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_delay must be positive, got: {v}")
        return v

    @field_validator("multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        if v < 1:
            raise ValueError(f"multiplier must be >= 1, got: {v}")
        return v

    @field_validator("jitter")
    @classmethod
    def validate_jitter(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"jitter must be non-negative, got: {v}")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"max_retries must be non-negative, got: {v}")
        return v


class ThenvoiBridge:
    """Main bridge orchestrator.

    Connects to the Band platform via WebSocket, listens for @mention
    messages, and routes them to the appropriate handler.
    """

    _DEDUP_MAX_SIZE = 10_000

    def __init__(
        self,
        config: BridgeConfig,
        handlers: dict[str, Handler],
        reconnect_config: ReconnectConfig | None = None,
    ) -> None:
        """Initialize the bridge.

        Args:
            config: Bridge configuration.
            handlers: Map of handler name -> handler instance.
            reconnect_config: Optional reconnection backoff config.

        Raises:
            ValueError: If agent_mapping references handler names not in handlers dict.
        """
        self._config = config
        self._handlers = handlers
        self._reconnect = reconnect_config or ReconnectConfig()
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._participant_cache: dict[str, list[ParticipantRecord]] = {}
        self._participant_lock = asyncio.Lock()
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

        # Parse and validate agent mapping
        self._agent_mapping = MentionRouter.parse_agent_mapping(config.agent_mapping)
        self._validate_handlers()

        # Create SDK link
        self._link = ThenvoiLink(
            agent_id=config.agent_id,
            api_key=config.api_key,
            ws_url=config.ws_url,
            rest_url=config.rest_url,
        )

        # Session store — default 24h TTL prevents leaks if room-removed events
        # are missed during network interruptions. TTL of 0 disables eviction.
        effective_ttl = config.session_ttl if config.session_ttl > 0 else None
        self._session_store = InMemorySessionStore(session_ttl=effective_ttl)

        # Router
        effective_timeout = (
            config.handler_timeout if config.handler_timeout > 0 else None
        )
        self._router = MentionRouter(
            agent_mapping=self._agent_mapping,
            handlers=self._handlers,
            session_store=self._session_store,
            agent_id=config.agent_id,
            link=self._link,
            handler_timeout=effective_timeout,
        )

        # Health server
        self._health = HealthServer(
            self._link,
            port=config.health_port,
            host=config.health_host,
            session_store=self._session_store,
            handler_count=len(self._handlers),
        )

    @property
    def _shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def _validate_handlers(self) -> None:
        """Validate that all mapped handler names have registered handlers."""
        for agent_name, handler_name in self._agent_mapping.items():
            if handler_name not in self._handlers:
                raise ValueError(
                    f"Agent '{agent_name}' maps to handler '{handler_name}', "
                    f"but no handler with that name is registered. "
                    f"Available handlers: {list(self._handlers.keys())}"
                )

    async def run(self) -> None:
        """Start the bridge and run until shutdown.

        Starts the health server and event loop concurrently.
        Handles SIGINT/SIGTERM for graceful shutdown.
        """
        loop = asyncio.get_running_loop()

        # add_signal_handler is only available on Unix (Linux/macOS).
        # On Windows this raises NotImplementedError; fall back to no-op.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                logger.debug(
                    "Signal handler for %s not supported on this platform", sig.name
                )

        if not self._handlers:
            logger.warning("Bridge starting with no handlers registered")

        logger.info(
            "Starting bridge with agent_id=%s, mapping=%s",
            self._config.agent_id,
            self._agent_mapping,
        )

        try:
            await self._health.start()
            await self._run_with_reconnect()
        finally:
            await self._shutdown()

    def _request_shutdown(self) -> None:
        """Signal handler for graceful shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("Shutdown requested")
            self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Perform graceful shutdown."""
        logger.info("Shutting down bridge...")
        try:
            await self._link.disconnect()
        except Exception:
            logger.warning("Error during link disconnect", exc_info=True)
        await self._health.stop()
        logger.info("Bridge shutdown complete")

    async def _run_with_reconnect(self) -> None:
        """Run the event loop with exponential backoff reconnection."""
        delay = self._reconnect.initial_delay
        attempts = 0

        while not self._shutting_down:
            self._connected_event.clear()
            try:
                await self._connect_and_consume()
                break  # Clean exit
            except Exception:
                if self._shutting_down:
                    break

                # If the connection was established before the failure, this
                # is a runtime disconnect (not a connection failure) — reset
                # backoff so the next reconnect attempt starts fresh.
                if self._connected_event.is_set():
                    delay = self._reconnect.initial_delay
                    attempts = 0

                attempts += 1
                if (
                    self._reconnect.max_retries > 0
                    and attempts >= self._reconnect.max_retries
                ):
                    logger.error(
                        "Max reconnect attempts (%d) reached, giving up",
                        self._reconnect.max_retries,
                    )
                    break

                logger.warning(
                    "Connection lost, reconnecting in %.1fs", delay, exc_info=True
                )

                # Ensure disconnected state before retry
                try:
                    await self._link.disconnect()
                except Exception:
                    logger.debug("Error during disconnect cleanup", exc_info=True)

                # Full-jitter backoff: sleep is uniform over [0, delay] so
                # multiple bridge instances spread their retries evenly.
                # When jitter is disabled (0) we fall back to deterministic
                # exponential backoff.
                if self._reconnect.jitter > 0:
                    sleep_time = random.uniform(0, delay)  # noqa: S311
                else:
                    sleep_time = delay
                await asyncio.sleep(sleep_time)

                # Exponential growth of the base delay (jitter does not
                # accumulate — it only affects the sleep above).
                delay = min(
                    delay * self._reconnect.multiplier,
                    self._reconnect.max_delay,
                )

        logger.info("Reconnect loop exited")

    async def _safe_handle_event(self, event: object) -> None:
        """Wrapper around ``_handle_event`` that catches and logs exceptions.

        Used as the target for fire-and-forget tasks so that a single handler
        failure does not break the event loop or propagate into the task set.
        """
        try:
            await self._handle_event(event)
        except Exception:
            logger.warning(
                "Unexpected error handling event %s",
                type(event).__name__,
                exc_info=True,
            )

    async def _connect_and_consume(self) -> None:
        """Connect to platform and consume events."""
        # Clear stale participant data from the previous connection.
        # The cache is re-populated below via _cache_room_participants
        # after re-subscribing to existing rooms.
        async with self._participant_lock:
            self._participant_cache.clear()
        await self._link.connect()
        self._connected_event.set()

        # Subscribe to agent room events (room added/removed)
        await self._link.subscribe_agent_rooms(self._config.agent_id)

        # Subscribe to existing rooms in parallel to reduce startup latency
        existing_rooms = await self._fetch_existing_rooms()
        if existing_rooms:
            await asyncio.gather(
                *[self._link.subscribe_room(rid) for rid in existing_rooms]
            )
            # Pre-populate participant cache to avoid per-message REST calls
            await asyncio.gather(
                *[self._cache_room_participants(rid) for rid in existing_rooms]
            )
            logger.info("Subscribed to %d existing rooms", len(existing_rooms))

        logger.info("Bridge connected and listening for events")

        # Race each event against the shutdown signal so the loop exits
        # immediately when shutdown is requested, without polling.
        # Handlers are fired as background tasks so the loop can pull the
        # next event without waiting for the previous handler to finish.
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
                    # Defensive: ThenvoiLink.__anext__ currently blocks
                    # forever on Queue.get() and never raises this, but we
                    # handle it in case the implementation changes.
                    # The RuntimeError branch below covers the same case
                    # wrapped by CPython (PEP 479: StopAsyncIteration raised
                    # inside an async generator is wrapped in RuntimeError).
                    break
                except RuntimeError as e:
                    # CPython wraps StopAsyncIteration in RuntimeError when
                    # it escapes an async generator (PEP 479).  Same guard
                    # as the StopAsyncIteration branch above.
                    if isinstance(e.__cause__, StopAsyncIteration) or isinstance(
                        e.__context__, StopAsyncIteration
                    ):
                        break
                    raise
                next_fut = None

                # Fire handler as a background task so we can pull the next
                # event immediately without waiting for the handler to finish.
                task = asyncio.create_task(self._safe_handle_event(event))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
        finally:
            if not shutdown_fut.done():
                shutdown_fut.cancel()
            if next_fut is not None and not next_fut.done():
                next_fut.cancel()
            # Cancel all in-flight handler tasks on shutdown
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

    async def _fetch_existing_rooms(self) -> list[str]:
        """Fetch the list of rooms the agent is already in.

        Returns:
            List of room IDs.
        """
        try:
            response = await self._link.rest.agent_api_chats.list_agent_chats(
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            if response.data:
                return [room.id for room in response.data]
        except Exception:
            logger.warning("Failed to fetch existing rooms", exc_info=True)
        return []

    async def _handle_event(self, event: object) -> None:
        """Dispatch a platform event to the appropriate handler.

        Args:
            event: A PlatformEvent from the link.
        """
        match event:
            case MessageEvent(room_id=room_id, payload=payload) if room_id and payload:
                await self._on_message(room_id, payload)

            case RoomAddedEvent(room_id=room_id) if room_id:
                logger.info("Room added: %s", room_id)
                try:
                    await self._link.subscribe_room(room_id)
                except Exception:
                    logger.warning(
                        "Failed to subscribe to room %s", room_id, exc_info=True
                    )
                    return
                await self._cache_room_participants(room_id)

            case RoomRemovedEvent(room_id=room_id) if room_id:
                logger.info("Room removed: %s", room_id)
                try:
                    await self._link.unsubscribe_room(room_id)
                except Exception:
                    logger.warning(
                        "Failed to unsubscribe from room %s", room_id, exc_info=True
                    )
                async with self._participant_lock:
                    self._participant_cache.pop(room_id, None)
                await self._session_store.remove(room_id)

            case ParticipantAddedEvent(room_id=room_id, payload=payload) if (
                room_id and payload
            ):
                async with self._participant_lock:
                    cached = self._participant_cache.get(room_id)
                    if cached is not None and not any(
                        p["id"] == payload.id for p in cached
                    ):
                        cached.append(
                            ParticipantRecord(
                                id=payload.id,
                                name=payload.name,
                                type=payload.type,
                                handle=getattr(payload, "handle", None),
                            )
                        )

            case ParticipantRemovedEvent(room_id=room_id, payload=payload) if (
                room_id and payload
            ):
                async with self._participant_lock:
                    cached = self._participant_cache.get(room_id)
                    if cached is not None:
                        self._participant_cache[room_id] = [
                            p for p in cached if p["id"] != payload.id
                        ]

            case _:
                logger.debug("Unhandled event: %s", type(event).__name__)

    async def _on_message(self, room_id: str, payload: MessageCreatedPayload) -> None:
        """Handle an incoming message event.

        Args:
            room_id: The room the message was received in.
            payload: MessageCreatedPayload from the platform.
        """
        # Deduplicate messages (reconnect may redeliver the same event)
        if self._is_duplicate(payload.id):
            logger.debug("Skipping duplicate message %s", payload.id)
            return

        # Quick pre-checks to avoid unnecessary AgentTools creation when
        # no handlers will be dispatched.  The router repeats these checks
        # authoritatively; these are just an optimisation.
        if payload.sender_id == self._config.agent_id:
            return
        if not payload.metadata or not payload.metadata.mentions:
            return

        # Use cached participants, fall back to REST on cache miss.
        # Shallow-copy inside the lock so concurrent ParticipantAdded/Removed
        # handlers mutating the list don't affect the snapshot used below.
        async with self._participant_lock:
            cached = self._participant_cache.get(room_id)
            participants = list(cached) if cached is not None else None
        if participants is None:
            try:
                participants = await self._get_room_participants(room_id)
                async with self._participant_lock:
                    self._participant_cache[room_id] = participants
            except Exception:
                logger.warning(
                    "Failed to fetch participants for room %s",
                    room_id,
                    exc_info=True,
                )
                participants = []

        # Resolve sender name and handle from participants
        sender_name: str | None = None
        sender_handle: str | None = None
        for p in participants:
            if p["id"] == payload.sender_id:
                sender_name = p.get("name")
                sender_handle = p.get("handle")
                break

        tools = AgentTools(
            room_id=room_id,
            rest=self._link.rest,
            participants=participants,
        )

        await self._router.route(
            payload,
            room_id,
            tools,
            sender_name=sender_name,
            sender_handle=sender_handle,
        )

    def _is_duplicate(self, message_id: str) -> bool:
        """Check if a message has already been processed (reconnect dedup).

        Uses a bounded OrderedDict so memory stays capped even under
        sustained high throughput.
        """
        if message_id in self._processed_message_ids:
            return True
        self._processed_message_ids[message_id] = None
        if len(self._processed_message_ids) > self._DEDUP_MAX_SIZE:
            self._processed_message_ids.popitem(last=False)
            logger.debug("Message ID dedup cache at max size, evicted oldest entry")
        return False

    async def _cache_room_participants(self, room_id: str) -> None:
        """Fetch and cache participants for a room.

        Errors are logged but do not propagate — the cache miss path in
        ``_on_message`` will fall back to a REST call per message.
        """
        try:
            fetched = await self._get_room_participants(room_id)
            async with self._participant_lock:
                self._participant_cache[room_id] = fetched
        except Exception:
            logger.warning(
                "Failed to cache participants for room %s", room_id, exc_info=True
            )

    async def _get_room_participants(self, room_id: str) -> list[ParticipantRecord]:
        """Fetch participants for a room.

        Args:
            room_id: The room ID.

        Returns:
            List of ParticipantRecord dicts with id, name, type.
        """
        response = (
            await self._link.rest.agent_api_participants.list_agent_chat_participants(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        )
        if not response.data:
            return []

        return [
            ParticipantRecord(
                id=p.id,
                name=p.name,
                type=p.type,
                handle=getattr(p, "handle", None),
            )
            for p in response.data
        ]


BandBridge = ThenvoiBridge


async def main(handlers: dict[str, Handler]) -> None:
    """Bridge entry point.

    Users should call this from their own script with registered handlers::

        import asyncio
        from bridge_core.bridge import main
        from my_handlers import MyHandler

        asyncio.run(main(handlers={"my_handler": MyHandler()}))

    Args:
        handlers: Map of handler name -> handler instance.
    """
    from dotenv import load_dotenv

    load_dotenv()

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    logger = logging.getLogger(__name__)
    try:
        config = BridgeConfig.from_env()
    except ValueError:
        logger.exception("Bridge configuration error")
        raise

    bridge = ThenvoiBridge(config=config, handlers=handlers)
    await bridge.run()
