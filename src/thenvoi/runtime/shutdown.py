"""
Graceful shutdown utilities for Thenvoi agents.

Provides signal handling and shutdown coordination for clean termination.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from thenvoi.agent import Agent

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """
    Manages graceful shutdown of a Thenvoi agent via signal handling.

    Catches SIGINT (Ctrl+C), SIGTERM, and SIGHUP signals and triggers a graceful
    shutdown of the agent, allowing current message processing to complete
    within a configurable timeout.

    Warning:
        The shutdown task created by signal handlers is NOT awaited. This means
        if the process exits immediately after receiving a signal (e.g., due to
        SIGKILL or rapid termination), the graceful shutdown may not complete.
        For guaranteed shutdown completion, use one of these patterns:

        1. Context manager (recommended):
            async with GracefulShutdown(agent, timeout=30.0):
                await agent.run()

        2. Manual await:
            await agent.stop(timeout=30.0)

    Note:
        Signal handlers using asyncio's add_signal_handler() only work on
        Unix systems (Linux, macOS). On Windows, use KeyboardInterrupt
        handling instead (asyncio.run() automatically converts SIGINT to
        KeyboardInterrupt):

            try:
                await agent.run()
            except KeyboardInterrupt:
                await agent.stop(timeout=30.0)

    Note:
        SIGHUP is treated as a shutdown signal, NOT a configuration reload
        signal. If your deployment expects SIGHUP for config reload, you
        should not use this handler and implement custom signal handling.

    Note:
        Multiple signals during shutdown: Once a shutdown is initiated, subsequent
        signals are ignored. The shutdown will complete within the configured timeout.
        If you need to force an immediate exit, send SIGKILL (kill -9) instead.

    Example (recommended pattern):
        agent = Agent.create(...)

        async def main():
            shutdown = GracefulShutdown(agent, timeout=30.0)
            shutdown.register_signals()

            try:
                await agent.run()
            except asyncio.CancelledError:
                pass  # Normal shutdown

        asyncio.run(main())

    Example (context manager pattern):
        async def main():
            agent = Agent.create(...)

            async with GracefulShutdown(agent, timeout=30.0):
                await agent.run()

    Example (manual signal handling):
        import logging
        logger = logging.getLogger(__name__)

        agent = Agent.create(...)

        async def main():
            shutdown = GracefulShutdown(agent, timeout=30.0)

            # Register custom signal behavior
            def on_signal(signum):
                logger.info("Received signal %s, shutting down...", signum)

            shutdown.on_signal = on_signal
            shutdown.register_signals()

            await agent.run()

    Attributes:
        agent: The Agent instance to shut down.
        timeout: Seconds to wait for graceful shutdown.
        on_signal: Optional callback invoked when signal is received.
    """

    def __init__(
        self,
        agent: "Agent",
        timeout: float = 30.0,
        on_signal: Callable[[int], None] | None = None,
    ):
        """
        Initialize graceful shutdown handler.

        Args:
            agent: The Agent instance to manage.
            timeout: Seconds to wait for processing to complete before
                     forcing shutdown. Default is 30 seconds.
            on_signal: Optional callback invoked when a shutdown signal
                       is received. Receives the signal number.
        """
        self.agent = agent
        self.timeout = timeout
        self.on_signal = on_signal
        self._shutdown_event: asyncio.Event | None = None
        self._original_handlers: dict[int, Any] = {}
        self._registered = False
        self._shutting_down = (
            False  # Guard against race between signal check and task creation
        )
        self._shutdown_task: asyncio.Task[None] | None = None

    def register_signals(self) -> None:
        """
        Register signal handlers for SIGINT and SIGTERM.

        On Unix systems, also handles SIGHUP (as shutdown, not reload).

        Call this before starting the agent. Signals will trigger
        a graceful shutdown of the agent.
        """
        if self._registered:
            return

        loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()

        # Register for SIGINT (Ctrl+C) and SIGTERM
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._original_handlers[sig] = signal.getsignal(sig)
            loop.add_signal_handler(sig, self._handle_signal, sig)

        # On Unix, also handle SIGHUP
        if hasattr(signal, "SIGHUP"):
            sig = signal.SIGHUP
            self._original_handlers[sig] = signal.getsignal(sig)
            loop.add_signal_handler(sig, self._handle_signal, sig)

        self._registered = True
        logger.debug("Graceful shutdown signal handlers registered")

    def unregister_signals(self) -> None:
        """
        Remove signal handlers and restore original handlers.

        Called automatically when using the context manager pattern.
        """
        if not self._registered:
            return

        loop = asyncio.get_running_loop()

        for sig in list(self._original_handlers.keys()):
            try:
                loop.remove_signal_handler(sig)
            except (ValueError, NotImplementedError):
                pass  # Signal handler was already removed or not supported

        self._original_handlers.clear()
        self._registered = False
        logger.debug("Graceful shutdown signal handlers unregistered")

    def _handle_signal(self, signum: int) -> None:
        """
        Handle received signal by initiating graceful shutdown.

        Args:
            signum: The signal number received.
        """
        sig_name = signal.Signals(signum).name

        # Guard FIRST — before any side effects. The documented contract is
        # "subsequent signals are ignored", and user-provided on_signal callbacks
        # (e.g. paging/alerting) may not be idempotent. _handle_signal runs in
        # event-loop callback context and never yields between the flag set and
        # create_task below, so checking the flag alone is sufficient.
        if self._shutting_down:
            logger.debug(
                "Received %s but shutdown already in progress, ignoring", sig_name
            )
            return
        self._shutting_down = True

        logger.info("Received %s, initiating graceful shutdown...", sig_name)

        # Call user callback if provided
        if self.on_signal:
            try:
                self.on_signal(signum)
            except Exception as e:
                logger.warning("Error in on_signal callback: %s", e)

        # Set shutdown event to unblock any waiters
        if self._shutdown_event:
            self._shutdown_event.set()

        # Schedule the shutdown coroutine.
        # Note: We store the reference to prevent garbage collection, but this task
        # is not awaited. If the process exits quickly after the signal (e.g., due to
        # a second signal or external termination), the shutdown may not fully complete.
        # For guaranteed completion, use the context manager pattern or await stop() manually.
        self._shutdown_task = asyncio.create_task(self._shutdown())

    async def _shutdown(self) -> None:
        """
        Perform graceful shutdown of the agent.

        Waits for current processing to complete within timeout,
        then stops the agent.
        """
        logger.info("Shutting down agent (timeout: %ss)...", self.timeout)

        try:
            # Shield agent.stop() so a cancellation propagating through the
            # outer task (e.g. a parent run() being cancelled) doesn't interrupt
            # cleanup mid-flight (WebSocket unsubscribe, state persistence, etc.).
            # Caveat: asyncio.run() teardown calls _cancel_all_tasks(), which
            # cancels the inner shielded task too — shield protects from the
            # outer awaiter, not full loop teardown. Use the context-manager or
            # await-stop patterns for guaranteed completion on process exit.
            graceful = await asyncio.shield(self.agent.stop(timeout=self.timeout))
            if graceful:
                logger.info("Agent shut down gracefully")
            else:
                logger.warning("Agent shut down with processing interrupted")
        except asyncio.CancelledError:
            # CancelledError inherits from BaseException (Py3.8+), so the
            # generic Exception handler below doesn't catch it. asyncio.shield
            # keeps the inner agent.stop() running in the background, so we do
            # NOT kick off a second concurrent stop here: AgentRuntime.stop
            # iterates executions.keys() with no in-flight guard, and two
            # concurrent stops would race on per-room teardown.
            logger.warning(
                "Shutdown was cancelled (timeout=%ss); shielded agent.stop "
                "continues in background",
                self.timeout,
            )
            raise
        except Exception as e:
            logger.error("Error during shutdown: %s", e, exc_info=True)

    async def wait_for_shutdown(self) -> None:
        """
        Wait until a shutdown signal is received.

        Useful for custom run loops:
            shutdown = GracefulShutdown(agent)
            shutdown.register_signals()
            await agent.start()
            await shutdown.wait_for_shutdown()
        """
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        await self._shutdown_event.wait()

    # --- Async context manager ---

    async def __aenter__(self) -> "GracefulShutdown":
        """Enter async context - register signal handlers."""
        self.register_signals()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context - unregister signal handlers."""
        self.unregister_signals()


async def run_with_graceful_shutdown(
    agent: "Agent",
    timeout: float = 30.0,
    on_signal: Callable[[int], None] | None = None,
) -> None:
    """
    Run an agent with graceful shutdown signal handling.

    Convenience function that sets up signal handlers and runs the agent.
    On SIGINT/SIGTERM, waits for current processing to complete within
    the timeout before stopping.

    Args:
        agent: The Agent instance to run.
        timeout: Seconds to wait for graceful shutdown.
        on_signal: Optional callback when signal is received.

    Example:
        agent = Agent.create(...)
        await run_with_graceful_shutdown(agent, timeout=30.0)
    """
    shutdown = GracefulShutdown(agent, timeout=timeout, on_signal=on_signal)

    async with shutdown:
        try:
            await agent.run(shutdown_timeout=timeout)
        except asyncio.CancelledError:
            # Normal shutdown via signal
            pass
