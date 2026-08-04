"""Run a fully initialized Parlant ``Server`` in-process.

Parlant's context-manager body is its configuration phase. Its normal
``Server.__aexit__`` then evaluates declared entities, installs retrievers, and
serves until cancelled. This module owns that whole lifecycle: it enters the
configuration phase, runs an optional setup callback, starts the normal exit path,
waits for the public readiness signal, and only then yields the server.

Keeping the exit task here gives one owner responsibility for cancellation and for
closing Parlant's private resource stack when startup fails before Parlant reaches
its own cleanup ``finally``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from band.integrations.parlant.ports import reserve_server_ports

# parlant is an optional extra, and this module is imported unconditionally
# (via band.adapters.parlant) in environments that cannot install it, such as
# the crewai dependency fork. Its absence surfaces on first use, not at import.
if TYPE_CHECKING:
    import parlant.sdk as p  # type: ignore[missing-import]
else:
    try:
        import parlant.sdk as p
    except ModuleNotFoundError:
        p = None

logger = logging.getLogger(__name__)

__all__ = ["running_parlant_server"]

_STARTUP_TIMEOUT_S = 120.0
_CLEANUP_TIMEOUT_S = 60.0

ServerSetup = Callable[["p.Server"], Awaitable[None]]


@asynccontextmanager
async def running_parlant_server(
    *,
    setup: ServerSetup | None = None,
    **server_kwargs: Any,
) -> AsyncGenerator[p.Server, None]:
    """Yield a fully set up, ready Parlant server and reliably stop it.

    ``server_kwargs`` are passed straight to ``p.Server(...)``. ``port`` and
    ``tool_service_port`` default to freshly reserved ephemeral ports. ``setup``
    runs in Parlant's configuration phase, before guideline evaluation and
    retriever installation; callers should declare agents, guidelines, journeys,
    and retrievers there.
    """
    if p is None:
        raise ImportError(
            "parlant is not installed; install band-sdk[parlant] to run a "
            "Parlant server"
        )

    if "port" not in server_kwargs or "tool_service_port" not in server_kwargs:
        ports = reserve_server_ports(server_kwargs.get("host"))
        server_kwargs.setdefault("port", ports.port)
        server_kwargs.setdefault("tool_service_port", ports.tool_service_port)

    server = p.Server(**server_kwargs)
    try:
        await server.__aenter__()
    except BaseException as exc:
        await _close_exit_stack(server)
        if isinstance(exc, SystemExit):
            raise ValueError(
                "Parlant server failed to start; see stderr above for the "
                "underlying configuration error"
            ) from exc
        raise

    try:
        if setup is not None:
            await setup(server)
    except BaseException as exc:
        await _abort_configuration(server, exc)
        raise

    exit_task = asyncio.create_task(
        server.__aexit__(None, None, None),
        name="parlant-server",
    )
    try:
        await _wait_until_ready(server, exit_task)
    except BaseException as exc:
        await _stop_server(server, exit_task, startup_error=exc)
        raise

    try:
        yield server
    finally:
        await _stop_server(server, exit_task)


async def _wait_until_ready(server: p.Server, exit_task: asyncio.Task[bool]) -> None:
    """Wait for readiness, while surfacing setup or serve failures immediately."""
    ready_task = asyncio.create_task(server.ready.wait())
    try:
        done, _ = await asyncio.wait(
            {ready_task, exit_task},
            timeout=_STARTUP_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if exit_task in done:
            await exit_task
            raise RuntimeError("Parlant server stopped before becoming ready")
        if ready_task not in done:
            raise TimeoutError(
                f"Parlant server did not become ready within {_STARTUP_TIMEOUT_S}s"
            )
    finally:
        ready_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ready_task


async def _abort_configuration(server: p.Server, exc: BaseException) -> None:
    """Exit a successfully entered server whose configuration failed."""
    try:
        await server.__aexit__(type(exc), exc, exc.__traceback__)
    except BaseException:
        logger.exception("Parlant cleanup after configuration failure also failed")


async def _stop_server(
    server: p.Server,
    exit_task: asyncio.Task[bool],
    *,
    startup_error: BaseException | None = None,
) -> None:
    """Cancel Parlant's serve loop and await its cleanup before returning."""
    if not exit_task.done():
        exit_task.cancel()
    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_S):
            try:
                await exit_task
            except asyncio.CancelledError:
                pass
            except BaseException:
                if startup_error is None:
                    raise
    except TimeoutError:
        logger.warning(
            "Parlant server cleanup did not finish within %ss; resources may "
            "remain open until process exit",
            _CLEANUP_TIMEOUT_S,
        )

    # Evaluation/retriever failures occur before Parlant's cleanup finally. The
    # dependency leaves both stacks open in that case, so close them here.
    if startup_error is not None and not server.ready.is_set():
        await _close_startup_context(server, startup_error)
        await _close_exit_stack(server)


async def _close_startup_context(server: p.Server, exc: BaseException) -> None:
    startup_cm = getattr(server, "_startup_context_manager", None)
    if startup_cm is None:
        return
    try:
        await startup_cm.__aexit__(type(exc), exc, exc.__traceback__)
    except BaseException:
        # It may already have unwound itself. The server-owned exit stack below is
        # independent and remains safe to close.
        pass


async def _close_exit_stack(server: p.Server) -> None:
    exit_stack = getattr(server, "_exit_stack", None)
    if exit_stack is None:
        return
    try:
        await exit_stack.aclose()
    except BaseException:
        logger.exception("Failed to close Parlant's server resource stack")
