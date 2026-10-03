"""The embedded MCP front door: run one ``LocalMCPServer`` per adapter.

Ephemeral-port scanning starts from a random offset (dodges a just-freed-
port wedge). Mounts ``engine.py``'s FastMCP app; ``engine.py`` also builds
the tool-registration list, this module only runs the server once it has one.

Every ``start()``/``stop()`` routes through one lock with cleanup in
``finally``, so a serve-task crash always closes the socket and resets
state, and concurrent start/stop calls can't race.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from collections.abc import Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import Self
from urllib.parse import quote

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import BaseRoute, Mount, Route

from band.integrations.mcp.engine import (
    ROOM_PATH_PARAM,
    EngineSpec,
    MCPToolRegistration,
    build_engine,
    validate_unique_tool_names,
)
from band.integrations.uvicorn_server import (
    SERVER_START_TIMEOUT_S,
    SERVER_STOP_TIMEOUT_S,
    wait_until_started,
)

logger = logging.getLogger(__name__)

LOCAL_MCP_HOST = "127.0.0.1"
LOCAL_MCP_PORT_MIN = 50000
LOCAL_MCP_PORT_MAX = 60000
LOCAL_MCP_SSE_PATH = "/sse"
LOCAL_MCP_HTTP_PATH = "/mcp"
LOCAL_MCP_MESSAGE_PATH = "/messages/"
LOCAL_MCP_HEALTH_PATH = "/healthz"
LOCAL_MCP_ROOMS_PATH = "/rooms"

# The process-global sse_starlette shutdown-drain footgun (see
# band.integrations.uvicorn_server's docstring) is disabled by importing
# that module above, not here -- a Windows CI hang was traced to exactly
# this before that fix existed.


class EmbeddedUvicornServer(uvicorn.Server):
    """A uvicorn server that leaves process signal handling to its host.

    uvicorn's ``serve()`` captures SIGINT/SIGTERM by default -- wrong for a
    server embedded in a host that already owns signal handling, and it's
    the other half of the sse_starlette footgun (see uvicorn_server's
    docstring): capturing signals here would let sse_starlette latch its
    shutdown flag through this server's handler too. Shutdown goes through
    ``should_exit`` instead (see ``LocalMCPServer.stop``).
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

    A ``room_bound`` server serves its endpoints under
    ``/rooms/{room_id}/`` instead of the root, for tool registrations that
    take their room from the request path (``room_from_connection``);
    address it with ``room_sse_url``/``room_http_url``.

    Lifecycle is an async context manager (``async with LocalMCPServer(...)
    as server:``); ``start()``/``stop()`` remain as the escape hatch for
    non-lexical lifetimes (``BandMCPBackend`` in ``backends.py`` holds its
    server across method scopes and genuinely needs them) -- they're the
    context manager's own halves, not a second code path.
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
        room_bound: bool = False,
        avoid_port: int | None = None,
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
        self._room_bound = room_bound
        self._avoid_port = avoid_port
        self._tool_registrations = registrations

        self._lifecycle_lock = asyncio.Lock()
        self._uvicorn_server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self._port: int | None = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    @property
    def port(self) -> int:
        """The port the last ``start()`` bound. A stopped server keeps it, so
        the URLs it handed out still read as the ones it served."""
        if self._port is None:
            raise RuntimeError("Local MCP server has not started")
        return self._port

    @property
    def url(self) -> str:
        return self.sse_url

    @property
    def sse_url(self) -> str:
        self._require_room_bound(False)
        return f"{self._origin}{self._sse_path}"

    @property
    def http_url(self) -> str:
        self._require_room_bound(False)
        return f"{self._origin}{self._http_path}"

    def room_sse_url(self, room_id: str) -> str:
        return f"{self._room_origin(room_id)}{self._sse_path}"

    def room_http_url(self, room_id: str) -> str:
        return f"{self._room_origin(room_id)}{self._http_path}"

    @property
    def _origin(self) -> str:
        return f"http://{self._host}:{self.port}"

    def _room_origin(self, room_id: str) -> str:
        self._require_room_bound(True)
        return f"{self._origin}{LOCAL_MCP_ROOMS_PATH}/{quote(room_id, safe='')}"

    def _require_room_bound(self, expected: bool) -> None:
        if self._room_bound == expected:
            return
        if self._room_bound:
            raise ValueError(
                f"Local MCP server {self._name} is room-bound; "
                "address it with room_http_url/room_sse_url"
            )
        raise ValueError(
            f"Local MCP server {self._name} is multi-room; "
            "address it with http_url/sse_url"
        )

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
            # Tracked immediately: stop()'s cleanup closes self._socket
            # unconditionally, so a failure below still gets it closed
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

                await wait_until_started(
                    uvicorn_server, serve_task, timeout_s=SERVER_START_TIMEOUT_S
                )
            except BaseException:
                # Cancellation too: no caller holds a server whose start never
                # returned, so a serve task left running here could never stop.
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

        Cleanup lives in ``finally`` -- a bare ``await self._serve_task``
        outside one would re-raise past the socket-close/state-reset code
        below it if the serve task crashed with anything but
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

    def _build_app(self, mcp: FastMCP) -> Starlette:
        """Mount the engine's SSE + streamable-HTTP routes onto one host app.

        ``streamable_http_app()`` lazily creates ``mcp.session_manager``, but
        a mounted sub-app's lifespan is never invoked by the ASGI server --
        only the top-level app's is. So the host lifespan below enters
        ``session_manager.run()`` itself.

        A room-bound server nests the engine's routes in one ``Mount`` whose
        path parameter every request carries in ``path_params``. FastMCP's
        own ``mount_path`` stays at its default: the SSE transport already
        advertises its message endpoint under the request's ``root_path``,
        so setting it too would double the prefix.
        """
        engine_routes: list[BaseRoute] = [
            *mcp.sse_app().routes,
            *mcp.streamable_http_app().routes,
        ]
        if self._room_bound:
            engine_routes = [
                Mount(
                    f"{LOCAL_MCP_ROOMS_PATH}/{{{ROOM_PATH_PARAM}}}",
                    routes=engine_routes,
                )
            ]

        async def healthz(_: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        @asynccontextmanager
        async def lifespan(_: Starlette):
            async with mcp.session_manager.run():
                yield

        return Starlette(
            lifespan=lifespan,
            routes=[
                *engine_routes,
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

        last_error: OSError | None = None
        for port in self._candidate_ports():
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

    def _candidate_ports(self) -> list[int]:
        """Every port in range, from a random offset, ``avoid_port`` last.

        Random rather than first-fit from port_min: first-fit reuses the port a
        just-stopped sibling freed, and that port's old consumers (an MCP
        client subprocess still winding down) keep sending stale traffic that
        wedges the new server's transport. ``avoid_port`` is such a port that
        the caller knows of, taken only when nothing else in range is free.
        """
        span = self._port_max - self._port_min + 1
        start = random.randrange(span)
        ports = [self._port_min + (start + offset) % span for offset in range(span)]
        return sorted(ports, key=lambda port: port == self._avoid_port)
