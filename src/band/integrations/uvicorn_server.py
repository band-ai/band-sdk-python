"""Shared lifecycle for integrations that embed their own uvicorn server.

``wait_until_started`` and ``ManagedUvicornServer`` are used by
``band.integrations.mcp.local_server``, ``band.integrations.a2a.gateway.server``,
and the A2A baseline test fixture, so the correctness-sensitive pieces --
surfacing a serve task that never came up, and cleaning up after a failed
start -- are each fixed in one place, not re-derived per caller.

Importing this module also disables sse_starlette's automatic
graceful-drain watcher (see the ``AppStatus`` call below): every consumer
here embeds its own ``uvicorn.Server`` the same way, and that watcher's
shutdown signal is a bare process-global with no notion of "which server."
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from sse_starlette.sse import AppStatus
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.05

# sse_starlette's shutdown watcher polls whichever uvicorn.Server owns the
# process's SIGTERM slot and promotes its should_exit to the process-global
# AppStatus.should_exit -- so one embedded server's stop() (which sets
# should_exit directly, not via a signal) can poison every other embedded
# server's SSE streams in the same process. Every caller here embeds its own
# uvicorn.Server this same way, so this is disabled once, on import, rather
# than duplicated per caller.
AppStatus.disable_automatic_graceful_drain()


async def wait_until_started(
    server: uvicorn.Server,
    serve_task: asyncio.Task[object],
    *,
    timeout_s: float,
) -> None:
    """Block until ``server`` reports ready.

    ``serve_task`` only returns once the server stops, so readiness is
    polled via ``server.started`` instead of awaiting the task directly.
    But a task that ends before ever setting ``started`` -- raising (a port
    already in use, a bad TLS config) or not (an early shutdown signal) --
    means the server will never start; either way that's fatal immediately,
    not something worth busy-waiting the full timeout to discover.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not server.started:
        if serve_task.done():
            await serve_task  # re-raises if the task itself failed
            raise RuntimeError("uvicorn server task ended before ever starting")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                f"uvicorn server did not report ready within {timeout_s}s"
            )
        await asyncio.sleep(POLL_INTERVAL_S)


class ManagedUvicornServer:
    """Runs one ASGI app on a background uvicorn server.

    Starts it, waits for readiness, tears it down -- no knowledge of what
    the app is or does.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        host: str,
        port: int,
        start_timeout_s: float,
        stop_timeout_s: int,
    ) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._start_timeout_s = start_timeout_s
        self._stop_timeout_s = stop_timeout_s
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def bound_port(self) -> int:
        """The actual listening port -- resolves ``port=0`` to whatever the
        OS assigned."""
        if self._server is None:
            raise RuntimeError("server has not started")
        return self._server.servers[0].sockets[0].getsockname()[1]

    async def start(self) -> None:
        server = uvicorn.Server(
            uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level="warning",
                timeout_graceful_shutdown=self._stop_timeout_s,
            )
        )
        task = asyncio.create_task(server.serve())
        self._server = server
        self._task = task
        try:
            await wait_until_started(server, task, timeout_s=self._start_timeout_s)
        except BaseException:
            # A failed/timed-out startup still leaves the task running and
            # the socket bound; stop() unwinds both.
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        # Ask uvicorn to exit rather than cancelling serve(): cancellation
        # skips its shutdown phase and leaks the listening socket.
        self._server.should_exit = True
        try:
            await self._task
        except asyncio.CancelledError:
            raise
        except BaseException:  # uvicorn raises SystemExit on startup failure
            logger.exception("Embedded uvicorn server exited with error")
        self._server = None
        self._task = None
