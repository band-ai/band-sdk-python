"""Shared lifecycle for integrations that embed their own uvicorn server.

Used by ``mcp.local_server``, ``a2a.gateway.server``, and the A2A baseline
test fixture. Importing this module also disables sse_starlette's
automatic graceful-drain watcher (see the ``AppStatus`` call below): its
shutdown signal is a bare process-global with no notion of "which server,"
so every embedder needs it disabled once, not per caller.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from sse_starlette.sse import AppStatus
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.05

# How long start() waits for uvicorn to report ready -- without it, a caller
# dialing in right after start() returns could race a socket that isn't
# listening yet.
SERVER_START_TIMEOUT_S = 5.0

# uvicorn's own default (None) waits forever for an open connection to close
# on stop() -- fatal for a caller holding one open on purpose (a live
# message:stream response, an MCP client's long-lived /sse GET). Bound it so
# stop() force-closes the connection instead of hanging indefinitely.
SERVER_STOP_TIMEOUT_S = 5

# Process-global footgun -- see module docstring. Disabled once, on import.
AppStatus.disable_automatic_graceful_drain()


async def wait_until_started(
    server: uvicorn.Server,
    serve_task: asyncio.Task[object],
    *,
    timeout_s: float,
) -> None:
    """Block until ``server`` reports ready.

    Polls ``server.started`` since ``serve_task`` only returns once the
    server stops. A task that ends first -- raising or not -- means the
    server will never start, so that's fatal immediately rather than
    busy-waited to the timeout.
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
        start_timeout_s: float = SERVER_START_TIMEOUT_S,
        stop_timeout_s: int = SERVER_STOP_TIMEOUT_S,
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
