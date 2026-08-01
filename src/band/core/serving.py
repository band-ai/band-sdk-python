"""One embedded HTTP server lifecycle, shared by the in-process transports."""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import nullcontext, suppress
from typing import Any, Literal

import uvicorn

from band.core.exceptions import LifecycleError

logger = logging.getLogger(__name__)

# A streaming GET (SSE) can stay open for the life of its session and may
# never close on its own. Bound the wait so stopping a server cannot hang a
# caller's shutdown indefinitely.
SERVER_STOP_TIMEOUT_S = 5

# uvicorn's graceful shutdown is the real bound on how long a stop takes; this
# is only the margin after it before we give up on the run returning at all.
# Too small and it fires first, adding its own wait on top of uvicorn's.
STOP_ABANDON_GRACE_S = 2


class EmbeddedServer:
    """An ASGI app served in this process, with a stop that means stopped.

    Telling uvicorn to exit is not the same as it having exited: until the
    run returns, the listening socket is still bound. Callers restart on the
    same port, so :meth:`stop` waits for the run to finish — and a run that
    ends by cancellation gets the shutdown uvicorn skips, since ``serve()``
    has no cleanup of its own on that path and would leave the port held.
    """

    def __init__(
        self,
        app: Any,
        *,
        host: str,
        port: int,
        log_level: str = "info",
        access_log: bool = True,
        lifespan: Literal["auto", "off", "on"] = "auto",
        sockets: list[socket.socket] | None = None,
        stop_timeout_s: int = SERVER_STOP_TIMEOUT_S,
    ) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._log_level = log_level
        self._access_log = access_log
        self._lifespan = lifespan
        self._sockets = sockets
        self._stop_timeout_s = stop_timeout_s
        self._server: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._background = False
        # Pre-set: with no run in flight there is nothing to wait for, and a
        # stop() that arrives before any start() must not block.
        self._finished = asyncio.Event()
        self._finished.set()

    @property
    def running(self) -> bool:
        """Whether a run currently holds the port."""
        return self._server is not None

    @property
    def serving(self) -> bool:
        """Whether the run has finished startup and is accepting requests."""
        return self._server is not None and bool(self._server.started)

    async def start(self) -> None:
        """Serve in the background until :meth:`stop`."""
        server = self._arm()
        self._background = True
        self._task = asyncio.create_task(self._run(server))

    async def serve(self) -> None:
        """Serve in the foreground until stopped or cancelled."""
        server = self._arm()
        self._background = False
        self._task = asyncio.current_task()
        await self._run(server)

    async def wait_until_serving(self, timeout_s: float) -> None:
        """Block until the run is accepting requests, or fail loudly.

        A run that dies during startup is reported as the error that killed
        it. Waiting out the timeout instead would hide the real cause in an
        unretrieved task exception and blame the clock.
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        while not self.serving:
            task = self._task
            if task is not None and task.done():
                await task  # re-raise whatever ended the run
                raise LifecycleError("server exited before it started serving")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"server did not start within {timeout_s}s")
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        """Ask the run to end, wait for it, and release the port."""
        server = self._server
        if server is None:
            return
        server.should_exit = True
        abandon_after = self._stop_timeout_s + STOP_ABANDON_GRACE_S
        if await self._finished_within(abandon_after):
            return
        # It ignored the request. A background run is ours to cancel; a
        # foreground one belongs to whoever is awaiting serve().
        task = self._task
        if task is None or not self._background:
            logger.warning(
                "Embedded server on port %s did not stop within %ss",
                self._port,
                abandon_after,
            )
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _arm(self) -> Any:
        """Claim the single run slot.

        Deliberately synchronous: with no await between clearing the event
        and publishing the server, a concurrent stop cannot land in the gap
        and find nothing to signal.
        """
        if self._server is not None:
            raise LifecycleError("embedded server is already running")
        self._finished.clear()
        self._task = None  # any handle left by a finished run
        self._background = False
        server = uvicorn.Server(
            uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level=self._log_level,
                access_log=self._access_log,
                lifespan=self._lifespan,
                timeout_graceful_shutdown=self._stop_timeout_s,
            )
        )
        # uvicorn's serve() captures SIGINT/SIGTERM for itself. Embedded in a
        # host that may run several servers over its lifetime, that hijacks
        # the host's signal handling and registers the server as process
        # state other libraries introspect: sse_starlette discovers "the"
        # uvicorn server through the installed handler and latches a
        # process-global shutdown flag when it stops mid-stream — after which
        # every later SSE response in the process closes right after its
        # headers. Shutdown here is driven through should_exit, so signal
        # capture is dropped entirely.
        server.capture_signals = nullcontext  # type: ignore[method-assign]
        self._server = server
        return server

    async def _run(self, server: Any) -> None:
        try:
            await server.serve(sockets=self._sockets)
        except BaseException:
            # A normal return means uvicorn already shut itself down; any
            # other exit skipped it and left the socket bound.
            await self._shutdown(server)
            raise
        finally:
            self._server = None
            # Handed-in sockets are closed by whichever shutdown ran; a later
            # run must bind its own rather than reuse the closed ones. The
            # run's task handle stays until the next run claims the slot, so
            # a startup wait can still see why the run ended.
            self._sockets = None
            self._finished.set()

    async def _shutdown(self, server: Any) -> None:
        if not server.started:
            # uvicorn closes handed-in sockets only from its own shutdown,
            # which a run that never finished starting never reaches.
            self._close_sockets()
            return
        server.should_exit = True
        try:
            await server.shutdown(sockets=self._sockets)
        except Exception:
            logger.exception("Embedded server shutdown after an aborted run failed")
            self._close_sockets()

    def _close_sockets(self) -> None:
        for handed_in in self._sockets or ():
            with suppress(OSError):
                handed_in.close()

    async def _finished_within(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(self._finished.wait(), timeout_s)
        except asyncio.TimeoutError:
            return False
        return True
