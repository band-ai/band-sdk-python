"""Agent - composes runtime, preprocessor, and adapter."""

from __future__ import annotations

import logging
import weakref
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

from band.core.protocols import FrameworkAdapter, Preprocessor
from band.core.simple_adapter import SimpleAdapter
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import (
    AgentConfig,
    ContactEventConfig,
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)
from band.preprocessing.default import DefaultPreprocessor

if TYPE_CHECKING:
    from band.platform.event import PlatformEvent
    from band.runtime.execution import ExecutionContext

logger = logging.getLogger(__name__)

try:
    _SDK_VERSION = _get_version("band-sdk")
except PackageNotFoundError:
    _SDK_VERSION = "unknown"

# Default graceful shutdown timeout in seconds
DEFAULT_SHUTDOWN_TIMEOUT: float = 30.0

# Agents started in this process and not yet stopped. Weak so a dropped
# agent never leaks through the registry; exists for harnesses whose
# failure paths can abandon an agent without unwinding it (e.g.
# pytest-timeout's signal kill), which need to find and stop it.
_running_agents: weakref.WeakSet[Agent] = weakref.WeakSet()


def running_agents() -> list[Agent]:
    """Agents started in this process that have not been stopped yet."""
    return list(_running_agents)


class _TimeoutNotSet:
    """Sentinel class to distinguish 'not set' from 'explicitly set to None'."""

    _instance: "_TimeoutNotSet | None" = None

    def __new__(cls) -> "_TimeoutNotSet":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<TIMEOUT_NOT_SET>"


# Singleton sentinel instance
_TIMEOUT_NOT_SET: _TimeoutNotSet = _TimeoutNotSet()

# Type alias for shutdown timeout (float, None, or sentinel)
_ShutdownTimeout = float | None | _TimeoutNotSet


class Agent:
    """
    Composes platform runtime, preprocessor, and adapter.

    Two ways to create:

    1. Full composition (power users):
        agent = Agent(
            runtime=PlatformRuntime(...),
            preprocessor=CustomPreprocessor(),
            adapter=MyAdapter(),
        )

    2. Simple factory (most users):
        agent = Agent.create(
            adapter=MyAdapter(),
            agent_id="...",
            api_key="...",
        )
    """

    def __init__(
        self,
        runtime: PlatformRuntime,
        adapter: FrameworkAdapter | SimpleAdapter,
        preprocessor: Preprocessor | None = None,
    ):
        self._runtime = runtime
        self._adapter = adapter
        self._preprocessor = preprocessor or DefaultPreprocessor()
        self._started = False
        # Tracks shutdown_timeout from run() for use in __aexit__
        # Uses sentinel to distinguish "not set" from "explicitly set to None"
        self._shutdown_timeout: _ShutdownTimeout = _TIMEOUT_NOT_SET

    @classmethod
    def create(
        cls,
        adapter: FrameworkAdapter | SimpleAdapter,
        agent_id: str,
        api_key: str,
        ws_url: str = "wss://app.band.ai/api/v1/socket/websocket",
        rest_url: str = "https://app.band.ai",
        config: AgentConfig | None = None,
        session_config: SessionConfig | None = None,
        contact_config: ContactEventConfig | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
        preprocessor: Preprocessor | None = None,
    ) -> "Agent":
        """
        Create agent with default runtime.

        Convenience factory for most users.

        Args:
            adapter: Framework adapter (e.g., PydanticAIAdapter)
            agent_id: UUID of the agent
            api_key: API key for authentication
            ws_url: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
            rest_url: REST API URL (default: https://app.band.ai)
            config: Agent configuration options
            session_config: Session lifecycle configuration
            contact_config: Contact event handling configuration.
                            Controls how contact requests and updates are processed.
                            See ContactEventConfig for strategies (DISABLED, CALLBACK, HUB_ROOM).
            on_participant_added: Optional callback for participant_added events.
            on_participant_removed: Optional callback for participant_removed events.
            preprocessor: Custom event preprocessor (default: DefaultPreprocessor)
        """
        runtime = PlatformRuntime(
            agent_id=agent_id,
            api_key=api_key,
            ws_url=ws_url,
            rest_url=rest_url,
            config=config,
            session_config=session_config,
            contact_config=contact_config,
            on_participant_added=on_participant_added,
            on_participant_removed=on_participant_removed,
        )
        return cls(
            runtime=runtime,
            adapter=adapter,
            preprocessor=preprocessor,
        )

    @classmethod
    def from_config(
        cls,
        name: str,
        *,
        adapter: FrameworkAdapter | SimpleAdapter,
        config_path: str | Path | None = None,
        **kwargs: Any,
    ) -> "Agent":
        """
        Create an Agent from YAML config + a constructed adapter.

        Loads agent_id and api_key from YAML configuration.
        The adapter is constructed by the caller in Python code.

        Args:
            name: Agent key in the YAML config file.
            adapter: Pre-constructed framework adapter.
            config_path: Path to agent_config.yaml. If None, searches
                         the default locations.
            **kwargs: Additional keyword arguments passed to create().

        Returns:
            Configured Agent instance.
        """
        from band.config.loader import load_agent_config

        agent_id, api_key = load_agent_config(name, config_path=config_path)
        return cls.create(
            adapter=adapter,
            agent_id=agent_id,
            api_key=api_key,
            **kwargs,
        )

    @property
    def runtime(self) -> PlatformRuntime:
        return self._runtime

    @property
    def agent_name(self) -> str:
        return self._runtime.agent_name

    @property
    def agent_description(self) -> str:
        return self._runtime.agent_description

    @property
    def is_running(self) -> bool:
        """Check if agent is currently running."""
        return self._started

    @property
    def contact_config(self) -> ContactEventConfig:
        """Get the contact event configuration."""
        return self._runtime.contact_config

    @property
    def is_contacts_subscribed(self) -> bool:
        """Check if agent is subscribed to contact events."""
        return self._runtime.is_contacts_subscribed

    async def start(self) -> None:
        """Start agent."""
        if self._started:
            logger.warning("Agent already started")
            return

        # 0. Refuse a duplicate instance BEFORE the adapter boots anything
        # (subprocesses, on-disk sessions). runtime.start() re-claims
        # idempotently for callers driving PlatformRuntime directly.
        self._runtime.claim_single_instance()
        try:
            # 1. Initialize runtime (fetch metadata via REST, no WebSocket yet)
            await self._runtime.initialize()

            # 2. Initialize adapter with agent metadata BEFORE message processing
            setattr(self._adapter, "_band_agent_id", self._runtime.agent_id)
            await self._adapter.on_started(
                self._runtime.agent_name,
                self._runtime.agent_description,
            )

            # 3. NOW start message processing (connects WebSocket)
            try:
                await self._runtime.start(
                    on_execute=self._on_execute,
                    on_cleanup=self._adapter.on_cleanup,
                )
            except BaseException:
                # on_started may have acquired resources (e.g. a CLI runtime
                # subprocess); a failed start must release them — stop() won't
                # run for an agent that never started.
                await self._cleanup_adapter()
                raise
        except BaseException:
            # Idempotent: a failure inside runtime.start() already released.
            self._runtime.release_single_instance()
            raise

        self._started = True
        _running_agents.add(self)
        logger.info(
            "Agent started: %s (band-sdk %s)", self._runtime.agent_name, _SDK_VERSION
        )

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop agent with optional graceful timeout.

        If timeout is provided, waits up to that many seconds for any ongoing
        message processing to complete before stopping. If timeout is None,
        stops immediately by cancelling any in-progress processing.

        Args:
            timeout: Optional seconds to wait for graceful shutdown.
                     None means stop immediately.

        Returns:
            True if stopped gracefully (processing completed or was idle),
            False if had to cancel mid-processing after timeout.
        """
        if not self._started:
            return True

        try:
            graceful = await self._runtime.stop(timeout=timeout)
        finally:
            # Always release adapter-wide resources (e.g. a CLI runtime
            # subprocess, a self-hosted MCP server, an external registration),
            # even when the runtime fails to stop cleanly.
            await self._cleanup_adapter()
            self._started = False
            _running_agents.discard(self)
        logger.info(
            "Agent stopped: %s (graceful=%s)", self._runtime.agent_name, graceful
        )
        return graceful

    async def _cleanup_adapter(self) -> None:
        """Release adapter-wide resources, best-effort."""
        cleanup_all = getattr(self._adapter, "cleanup_all", None)
        if cleanup_all is not None:
            try:
                await cleanup_all()
            except Exception:
                logger.exception("Adapter cleanup_all failed")

    async def run(
        self, shutdown_timeout: float | None = DEFAULT_SHUTDOWN_TIMEOUT
    ) -> None:
        """
        Run until interrupted.

        Args:
            shutdown_timeout: Seconds to wait for graceful shutdown on interrupt.
                              Set to None for immediate cancellation.
                              Default is 30 seconds.
        """
        self._shutdown_timeout = shutdown_timeout
        await self.start()
        try:
            await self._runtime.run_forever()
        finally:
            await self.stop(timeout=shutdown_timeout)

    # --- Async context manager ---

    async def __aenter__(self) -> "Agent":
        """
        Enter async context - start the agent.

        Example:
            async with Agent.create(...) as agent:
                await agent.run_forever()  # or just wait
        """
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit async context - stop the agent gracefully.

        Uses the shutdown_timeout configured in run(), or DEFAULT_SHUTDOWN_TIMEOUT
        if run() was never called. If run() was called with shutdown_timeout=None,
        stops immediately without waiting.
        """
        # Use default only if run() was never called (sentinel value)
        # If run() was called with None, respect that (immediate cancellation)
        if self._shutdown_timeout is _TIMEOUT_NOT_SET:
            timeout: float | None = DEFAULT_SHUTDOWN_TIMEOUT
        else:
            # Cast is safe: at this point it's either float or None (not sentinel)
            timeout = cast(float | None, self._shutdown_timeout)
        await self.stop(timeout=timeout)

    async def run_forever(self) -> None:
        """
        Keep the agent running forever.

        Use this inside an async context manager:
            async with agent:
                await agent.run_forever()

        Or after manually calling start():
            await agent.start()
            try:
                await agent.run_forever()
            finally:
                await agent.stop()
        """
        await self._runtime.run_forever()

    async def _on_execute(
        self,
        ctx: "ExecutionContext",
        event: "PlatformEvent",
    ) -> None:
        """Handle platform event."""
        # Preprocessor is the single source of truth for event filtering.
        # It returns None for non-MessageEvent types.
        inp = await self._preprocessor.process(
            ctx=ctx,
            event=event,
            agent_id=self._runtime.agent_id,
        )

        if inp is None:
            return

        await self._adapter.on_event(inp)
