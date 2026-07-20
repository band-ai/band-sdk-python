"""Run an in-process Parlant ``Server`` for a test without its serve-forever exit.

Parlant's ``Server`` is a "configure in the body, then serve forever on exit"
context manager: ``__aenter__`` only builds the DI container, and ``__aexit__`` is
what boots uvicorn (``serve_app`` -> ``uvicorn_server.serve()``), which blocks
until a SIGINT/SIGTERM that never comes in-process. So a plain
``async with p.Server() as server: ...`` hangs on teardown and leaks the ports —
the failure that keeps Parlant out of the baseline matrix.

The band ``ParlantAdapter`` drives the engine in-process via the container (set up
by ``__aenter__``), so the HTTP serve phase is never needed for a turn to complete.
This helper therefore enters the server (setup only), yields it for the run, and at
teardown drives ``__aexit__`` *as a cancellable task*: it lets the serve loop come
up (waiting on the public ``ready`` event) and then cancels it. ``serve_app``
catches that ``CancelledError`` and returns, so ``__aexit__``'s ``finally`` runs the
real resource cleanup (``_exit_stack.aclose()`` shuts the plugin server on
``tool_service_port`` and the DB/evaluator) and the call returns instead of hanging.

Why not cancel a *parked body* instead (never serving)? That drives ``__aexit__``'s
exception branch, where ``await self._startup_context_manager.__aexit__(exc, ...)``
re-raises and skips ``_exit_stack.aclose()`` — leaking the plugin server and its
port. Only the no-exception branch runs that cleanup in a ``finally``.

Version note: this leans on Parlant's documented context-manager protocol plus the
public ``Server.ready`` event and ``serve_app``'s ``CancelledError`` handling. A
Parlant upgrade that reworks the ``__aexit__`` serve/cleanup path could need this
revisited; centralising it here keeps that to one place.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import parlant.sdk as p

logger = logging.getLogger(__name__)

# Generous ceiling for uvicorn to bind + serve + answer its first /healthz at
# teardown. Hitting it means we cancel a not-yet-serving exit (best-effort cleanup)
# rather than hang the suite.
_READY_TIMEOUT_S = 120.0


def _reserve_two_ports() -> tuple[int, int]:
    """Reserve two distinct loopback ports from the OS, then release them.

    Parlant's default ports (8800/8818) collide under ``flaky`` reruns (the prior
    server may not have released them yet) or two concurrent E2E sessions on one
    host. Binding both sockets at once guarantees the OS hands back distinct ports;
    we close them before passing the numbers to Parlant, which re-binds them itself.
    The close->rebind gap is the standard ephemeral-port reservation race — far
    smaller than the collision risk of two fixed ports (mirrors
    ``mcp_server._reserve_socket``).
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as a,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as b,
    ):
        a.bind(("", 0))
        b.bind(("", 0))
        return a.getsockname()[1], b.getsockname()[1]


@asynccontextmanager
async def running_parlant_server(
    **server_kwargs: Any,
) -> AsyncGenerator[p.Server, None]:
    """Yield a ready in-process Parlant ``Server``, tearing it down without hanging.

    ``server_kwargs`` are passed straight to ``p.Server(...)``. Two defaults are
    filled in when the caller omits them, since every E2E caller wants the same
    thing:

    * ``nlp_service`` defaults to ``p.NLPServices.openai`` — it authenticates with
      ``OPENAI_API_KEY`` (which the E2E env provides), whereas Parlant otherwise
      defaults to Emcie's hosted service (``EMCIE_API_KEY``, which the env doesn't
      set).
    * ``port`` / ``tool_service_port`` default to freshly reserved ephemeral ports
      (see ``_reserve_two_ports``) so reruns / concurrent sessions don't collide.

    The agent the caller builds on the yielded server runs against its in-process
    container; the HTTP server only ever comes up briefly during teardown, purely so
    Parlant's own cleanup ``finally`` can run.

    NOTE: Parlant processes guideline/journey evaluations and retriever setup at the
    top of ``__aexit__``, i.e. only at teardown here — the same as the stock
    ``async with p.Server()`` body, which the adapter never depended on. For an agent
    with no guidelines/journeys/retrievers these are no-ops (band injects tools
    per-session at runtime), so a turn behaves identically to production.
    """
    server_kwargs.setdefault("nlp_service", p.NLPServices.openai)
    if "port" not in server_kwargs or "tool_service_port" not in server_kwargs:
        port, tool_service_port = _reserve_two_ports()
        server_kwargs.setdefault("port", port)
        server_kwargs.setdefault("tool_service_port", tool_service_port)

    server = p.Server(**server_kwargs)
    await server.__aenter__()  # setup only: build the DI container, no serving yet
    try:
        yield server
    finally:
        await _shutdown_without_hanging(server)


async def _shutdown_without_hanging(server: p.Server) -> None:
    """Run ``Server.__aexit__`` for its cleanup, cancelling its serve loop."""
    exit_task = asyncio.create_task(server.__aexit__(None, None, None))
    ready_task = asyncio.create_task(server.ready.wait())
    try:
        # Either the server starts serving (ready fires) or __aexit__ errors out of
        # its pre-serve setup; react to whichever happens first.
        await asyncio.wait(
            {exit_task, ready_task},
            timeout=_READY_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        ready_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ready_task

    if not exit_task.done():
        # Ready (we're inside __aexit__'s try, so its finally will run
        # _exit_stack.aclose()) or timed out (best-effort). Cancel the serve loop;
        # serve_app swallows the CancelledError, so __aexit__ returns cleanly.
        exit_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await exit_task  # re-raises a genuine pre-serve setup error, if any
