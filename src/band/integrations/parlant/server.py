"""Run an in-process Parlant ``Server`` without its serve-forever exit.

Parlant's ``Server`` is a "configure in the body, then serve forever on exit"
context manager: ``__aenter__`` only builds the DI container, and ``__aexit__`` is
what boots uvicorn (``serve_app`` -> ``uvicorn_server.serve()``), which blocks
until a SIGINT/SIGTERM. A plain ``async with p.Server() as server: ...`` therefore
hangs on teardown when nothing interrupts the process.

The band ``ParlantAdapter`` drives the engine in-process via the container (set up
by ``__aenter__``), so the HTTP serve phase is never needed at all. This helper
enters the server (setup only), yields it for the run, and at teardown drives
``Server.__aexit__``'s *exception branch* with a sentinel exception: handed an
exception, ``__aexit__`` skips evaluation processing and the serve loop entirely
and goes straight to cleanup. The startup context manager unwinds ``load_app``'s
exit stack with the exception set — ``BackgroundTaskService.__aexit__`` cancels
its tasks on exactly that signal, then the stores/databases/tracers close — each
generator re-raises the same sentinel instance (so ``contextlib`` returns instead
of raising), and ``__aexit__`` finally closes the server's own ``_exit_stack``
(the plugin tool server on ``tool_service_port``). uvicorn never starts, so there
is nothing to hang on, cancel, or race.

Version note: verified against parlant 3.3.2, the pinned floor — its exception
branch runs both the startup-context unwind and ``_exit_stack.aclose()``, and
``BackgroundTaskService`` cancels its tasks on an exceptional exit. A Parlant
upgrade that reworks the ``__aexit__`` cleanup path could need this revisited;
centralising it here keeps that to one place.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
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

# Generous ceiling for Parlant's real teardown work: cancelling background tasks,
# closing stores/databases and the plugin tool server. Hitting it cancels whatever
# cleanup await is stuck (best-effort) rather than hanging the caller.
_CLEANUP_TIMEOUT_S = 60.0


class ServerTeardown(BaseException):
    """Sentinel driving ``Server.__aexit__``'s no-serve (exception) branch.

    ``BaseException``-derived so no ``except Exception`` inside Parlant's
    teardown can swallow it and resume toward the serve loop.
    """


@asynccontextmanager
async def running_parlant_server(
    **server_kwargs: Any,
) -> AsyncGenerator[p.Server, None]:
    """Yield a ready in-process Parlant ``Server``, tearing it down without serving.

    ``server_kwargs`` are passed straight to ``p.Server(...)``. One default is
    filled in when the caller omits it: ``port`` / ``tool_service_port`` default to
    freshly reserved ephemeral ports (see ``reserve_server_ports``) so several
    agents on one host don't collide on Parlant's fixed defaults. Everything else —
    including ``nlp_service`` — keeps Parlant's own defaults.

    The agent the caller builds on the yielded server runs against its in-process
    container; the HTTP API server never comes up at all (see the module docstring
    for how teardown reaches Parlant's cleanup without it).
    """
    if p is None:
        raise ImportError(
            "parlant is not installed; install band-sdk[parlant] to run a "
            "Parlant server"
        )

    if "port" not in server_kwargs or "tool_service_port" not in server_kwargs:
        # Reserved on the host this server would bind, so the reservation covers
        # the interfaces the plugin tool server actually needs.
        ports = reserve_server_ports(server_kwargs.get("host"))
        server_kwargs.setdefault("port", ports.port)
        server_kwargs.setdefault("tool_service_port", ports.tool_service_port)

    server = p.Server(**server_kwargs)
    try:
        await server.__aenter__()  # setup only: builds the DI container, no serving
    except SystemExit as exc:
        # Parlant routes SDK/NLP configuration failures through sys.exit(1) after
        # printing the cause to stderr. Embedded in a host process — possibly
        # running other agents — that must not die with us: surface a catchable
        # error instead.
        raise ValueError(
            "Parlant server failed to start; see stderr above for the underlying "
            "configuration error"
        ) from exc
    try:
        yield server
    finally:
        await _teardown_without_serving(server)


async def _teardown_without_serving(server: p.Server) -> None:
    """Run ``Server.__aexit__``'s cleanup through its exception branch."""
    signal = ServerTeardown()
    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_S):
            # The sentinel is re-raised and re-caught inside Parlant's context
            # managers and must not escape to the caller even if one layer
            # re-raises it out of ``__aexit__``.
            with contextlib.suppress(ServerTeardown):
                await server.__aexit__(type(signal), signal, None)
    except TimeoutError:
        logger.warning(
            "Parlant server cleanup did not finish within %ss; abandoning it "
            "(resources may leak until process exit)",
            _CLEANUP_TIMEOUT_S,
        )
