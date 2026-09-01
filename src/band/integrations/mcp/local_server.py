"""The embedded MCP front door: run one ``LocalMCPServer`` per adapter.

Ephemeral-port scanning starts from a random offset (dodges a just-freed-
port wedge). Mounts ``engine.py``'s FastMCP app rather than hand-rolling a
lowlevel ``Server``; building the tool-registration list itself is
``engine.py``'s job too (``build_band_mcp_tool_registrations`` /
``build_resolved_band_mcp_tool_registrations``) -- this module only runs
the server once it has that list.

Every lifecycle transition (``start()``/``stop()``) routes through one lock,
with cleanup in ``finally`` -- so a serve-task crash always closes the
socket and resets state, and concurrent start/stop calls can't race.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from collections.abc import Generator, Sequence
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from band.integrations.mcp.engine import (
    EngineSpec,
    MCPToolRegistration,
    build_engine,
    validate_unique_tool_names,
)

logger = logging.getLogger(__name__)

LOCAL_MCP_HOST = "127.0.0.1"
LOCAL_MCP_PORT_MIN = 50000
LOCAL_MCP_PORT_MAX = 60000
LOCAL_MCP_SSE_PATH = "/sse"
LOCAL_MCP_HTTP_PATH = "/mcp"
LOCAL_MCP_MESSAGE_PATH = "/messages/"
LOCAL_MCP_HEALTH_PATH = "/healthz"
SERVER_START_TIMEOUT_S = 5.0
# uvicorn's own default (None) waits forever for existing connections to close
# on `stop()` -- fatal here, since an MCP client (e.g. OpenCode) holds its `/sse`
# GET open for the life of its session and may never close it on its own after
# we deregister. Bound it so `stop()` force-cancels that connection instead of
# hanging the adapter's cleanup indefinitely.
SERVER_STOP_TIMEOUT_S = 5

# sse_starlette's EventSourceResponse watches a process-global AppStatus for a
# shutdown signal, closing every open SSE stream right after its headers once
# latched -- from either of two sources: our own signal handler (neutralized
# by EmbeddedUvicornServer.capture_signals below), or *any other*
# uvicorn.Server anywhere in this process whose handle_exit() ever fires,
# since AppStatus.should_exit is a bare class attribute with no notion of
# "which server." The second case is real: a Windows CI hang traced to
# exactly this, a live SSE connection closing right after its headers with no
# code of ours involved. LocalMCPServer.stop() already forces its own socket
# closed and cancels its serve task directly, so it never needed
# sse_starlette's automatic drain-on-shutdown; disabling it here removes the
# dependency on that global entirely. Process-wide and one-time by nature
# (AppStatus has no per-instance scope), hence a module-level call rather than
# something threaded through LocalMCPServer's API.
#
# Cost of that global scope: any *other* sse_starlette consumer in the same
# process loses this same signal too, the moment this module is imported.
# The A2A gateway (src/band/integrations/a2a/gateway/server.py) hits the
# identical bug independently and disables this itself; the call here is
# redundant with that one but harmless (idempotent, process-wide).
AppStatus.disable_automatic_graceful_drain()


class EmbeddedUvicornServer(uvicorn.Server):
    """A uvicorn server that leaves process signal handling to its host.

    uvicorn's ``serve()`` captures SIGINT/SIGTERM for itself -- fine for a
    standalone process, but this server is embedded in a host that may run
    several servers over its lifetime and already owns its own signal
    handling. It's also the other half of the sse_starlette bug documented at
    the ``AppStatus.disable_automatic_graceful_drain()`` call above: capturing
    signals here would let sse_starlette latch its process-global shutdown
    flag through *this* server's handler too. Shutdown is driven
    programmatically instead, via ``should_exit`` (see ``LocalMCPServer.stop``).
    """

    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def _new_reusable_socket() -> socket.socket:
    """A TCP socket with ``SO_REUSEADDR`` set, not yet bound."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def _listen(sock: socket.socket) -> socket.socket:
    """Put an already-bound socket into non-blocking listen mode."""
    sock.listen(2048)
    sock.setblocking(False)
    return sock


class LocalMCPServer:
    """A local MCP server with SSE and streamable HTTP endpoints.

    Binds to loopback by default. An explicit non-loopback ``host`` (e.g.
    ``"0.0.0.0"``) is allowed for callers whose MCP client runs in a container
    and reaches back over the docker bridge -- but it exposes the agent's
    tools to the local network, so only opt in on an isolated/trusted host.

    Lifecycle is an async context manager (``async with LocalMCPServer(...)
    as server:``); ``start()``/``stop()`` remain as the escape hatch for
    non-lexical lifetimes (``acp/client_adapter.py`` holds its server across
    method scopes and genuinely needs them) -- they're the context manager's
    own halves, not a second code path.
    """

    def __init__(
        self,
        name: str,
        tool_registrations: Sequence[MCPToolRegistration],
        *,
        host: str = LOCAL_MCP_HOST,
        port_min: int = LOCAL_MCP_PORT_MIN,
        port_max: int = LOCAL_MCP_PORT_MAX,
        sse_path: str = LOCAL_MCP_SSE_PATH,
        http_path: str = LOCAL_MCP_HTTP_PATH,
        message_path: str = LOCAL_MCP_MESSAGE_PATH,
    ) -> None:
        if port_min > port_max:
            raise ValueError("port_min must be less than or equal to port_max")

        registrations = list(tool_registrations)
        validate_unique_tool_names(registrations)

        self._name = name
        self._host = host
        self._port_min = port_min
        self._port_max = port_max
        self._sse_path = sse_path
        self._http_path = http_path
        self._message_path = message_path
        self._tool_registrations = registrations

        self._lifecycle_lock = asyncio.Lock()
        self._uvicorn_server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self._port: int | None = None

    async def __aenter__(self) -> LocalMCPServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Local MCP server has not started")
        return self._port

    @property
    def url(self) -> str:
        return self.sse_url

    @property
    def sse_url(self) -> str:
        return f"http://{self._host}:{self.port}{self._sse_path}"

    @property
    def http_url(self) -> str:
        return f"http://{self._host}:{self.port}{self._http_path}"

    @property
    def is_running(self) -> bool:
        """False once the serve task has ended, crashed or not.

        A crash leaves every cached reference to this server (host/port,
        session config) pointing at a dead process; a caller holding one of
        those references checks this before reusing it.
        """
        return self._serve_task is not None and not self._serve_task.done()

    async def start(self) -> None:
        """Start the local MCP server."""
        async with self._lifecycle_lock:
            if self._serve_task and not self._serve_task.done():
                return

            reserved_socket, port = self._reserve_socket()
            # Tracked immediately, before anything below can raise: `stop()`'s
            # cleanup closes `self._socket` unconditionally, so a failure in
            # engine/app/uvicorn construction still gets the socket closed
            # instead of leaking a bound-and-listening fd.
            self._socket = reserved_socket
            self._port = port

            try:
                # A fresh FastMCP every start(): its session manager is
                # single-use (StreamableHTTPSessionManager.run() raises on a
                # second call), so a start->stop->start cycle needs a
                # brand-new engine, not a restarted one.
                mcp = build_engine(
                    EngineSpec(name=self._name, tools=tuple(self._tool_registrations)),
                    host=self._host,
                    sse_path=self._sse_path,
                    message_path=self._message_path,
                    streamable_http_path=self._http_path,
                )
                app = self._build_app(mcp)
                uvicorn_server = EmbeddedUvicornServer(
                    uvicorn.Config(
                        app,
                        host=self._host,
                        port=port,
                        lifespan="on",
                        log_level="warning",
                        access_log=False,
                        timeout_graceful_shutdown=SERVER_STOP_TIMEOUT_S,
                    )
                )
                serve_task = asyncio.create_task(
                    uvicorn_server.serve(sockets=[reserved_socket])
                )

                self._uvicorn_server = uvicorn_server
                self._serve_task = serve_task

                await self._wait_until_started()
            except Exception:
                await self._stop_locked()
                raise

            logger.info(
                "Started local MCP server %s on %s:%s with %s tools",
                self._name,
                self._host,
                self._port,
                len(self._tool_registrations),
            )

    async def stop(self) -> None:
        """Stop the local MCP server."""
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        """The actual teardown, run only while ``_lifecycle_lock`` is held.

        Cleanup lives in ``finally``: the previous version's bare ``await
        self._serve_task`` re-raised past the socket-close/state-reset code
        below it whenever the serve task crashed with anything but
        ``CancelledError``, leaking the socket and leaving stale state for
        the next ``start()``.
        """
        try:
            if self._uvicorn_server is not None:
                self._uvicorn_server.should_exit = True
            if self._serve_task is not None:
                try:
                    await self._serve_task
                except asyncio.CancelledError:
                    logger.debug("Local MCP server task cancelled for %s", self._name)
                except Exception:
                    logger.exception(
                        "Local MCP server %s serve task crashed", self._name
                    )
        finally:
            if self._socket is not None:
                self._socket.close()
            self._uvicorn_server = None
            self._serve_task = None
            self._socket = None
            self._port = None

    def _build_app(self, mcp: FastMCP) -> Starlette:
        """Mount the engine's SSE + streamable-HTTP routes onto one host app.

        ``streamable_http_app()`` lazily creates ``mcp.session_manager`` and
        returns its own Starlette app whose lifespan runs it -- but a mounted
        sub-app's lifespan is never invoked by the ASGI server, only the
        top-level app's is. So the host lifespan below enters
        ``session_manager.run()`` itself (verified by the step-1 spike).
        """
        sse_routes = list(mcp.sse_app().routes)
        http_routes = list(mcp.streamable_http_app().routes)

        async def healthz(_: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        @asynccontextmanager
        async def lifespan(_: Starlette):
            async with mcp.session_manager.run():
                yield

        return Starlette(
            lifespan=lifespan,
            routes=[
                *sse_routes,
                *http_routes,
                Route(LOCAL_MCP_HEALTH_PATH, endpoint=healthz, methods=["GET"]),
            ],
        )

    def _reserve_socket(self) -> tuple[socket.socket, int]:
        # Port 0 -> ask the OS for any free port (race-free, ideal for tests)
        if self._port_min == 0:
            reserved_socket = _new_reusable_socket()
            reserved_socket.bind((self._host, 0))
            port = reserved_socket.getsockname()[1]
            return _listen(reserved_socket), port

        # Scan the range from a random starting offset (wrapping around), not
        # first-fit from port_min: first-fit hands a new server the port a
        # just-stopped sibling freed moments ago, and that port's previous
        # consumers (e.g. an MCP client subprocess still winding down) keep
        # sending stale session traffic that wedges the new server's transport.
        last_error: OSError | None = None
        span = self._port_max - self._port_min + 1
        start = random.randrange(span)
        for offset in range(span):
            port = self._port_min + (start + offset) % span
            reserved_socket = _new_reusable_socket()
            try:
                reserved_socket.bind((self._host, port))
            except OSError as exc:
                last_error = exc
                reserved_socket.close()
                continue
            return _listen(reserved_socket), port

        raise RuntimeError(
            "Could not find a free localhost MCP port in range "
            f"{self._port_min}-{self._port_max}"
        ) from last_error

    async def _wait_until_started(self) -> None:
        if self._serve_task is None or self._uvicorn_server is None:
            raise RuntimeError("Local MCP server task not initialized")

        deadline = asyncio.get_running_loop().time() + SERVER_START_TIMEOUT_S
        while not self._uvicorn_server.started:
            if self._serve_task.done():
                await self._serve_task
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out waiting for local MCP server startup")
            await asyncio.sleep(0.05)
