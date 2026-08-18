"""The embedded MCP front door.

Ephemeral-port scanning starts from a random offset (dodges a just-freed-
port wedge), and ``EmbeddedUvicornServer`` disables signal capture (dodges
an ``sse_starlette`` global-shutdown-latch bug). Mounts ``engine.py``'s
FastMCP app rather than hand-rolling a lowlevel ``Server``.

Every lifecycle transition (``start()``/``stop()``) routes through one lock,
with cleanup in ``finally`` -- so a serve-task crash always closes the
socket and resets state, and concurrent start/stop calls can't race.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from collections.abc import Callable, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from band.core.protocols import AgentToolsProtocol
from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    MCPToolRegistration,
    build_custom_tool_registration,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
    validate_unique_tool_names,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools import Surface, ToolDefinition, iter_tool_definitions

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

RoomToolResolver = Callable[[str], AgentToolsProtocol | None]


class EmbeddedUvicornServer(uvicorn.Server):
    """A uvicorn server that leaves process signal handling to its host.

    uvicorn's ``serve()`` captures SIGINT/SIGTERM for itself. Embedded in a
    host process that may run several servers over its lifetime, that hijacks
    the host's signal handling, and registers the server as process state that
    other libraries introspect: sse_starlette discovers "the" uvicorn server
    through the installed signal handler and latches a process-global shutdown
    flag when it stops mid-stream -- after which every later SSE response in
    the process (any subsequent server's) closes right after its headers.
    Shutdown here is driven programmatically via ``should_exit`` (see
    ``LocalMCPServer.stop``), so signal capture is dropped entirely.
    """

    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def _filter_to_agent_surface(
    definitions: Sequence[ToolDefinition],
) -> list[ToolDefinition]:
    """Drop non-agent definitions and log a warning for each discarded entry.

    ``build_*_tool_registrations`` wire their execution path through
    ``AgentTools``; a ``surface="human"`` definition in the list would
    ``AttributeError`` at call time because ``AgentTools`` has no
    ``HumanTools`` methods. Rather than propagate the error, quietly filter
    and warn so a regression in a caller is observable but not fatal.
    """
    filtered: list[ToolDefinition] = []
    for definition in definitions:
        if definition.surface != Surface.AGENT:
            logger.warning(
                "Dropping non-agent tool definition %r (surface=%r) from MCP "
                "registrations; LocalMCPServer is agent-only.",
                definition.name,
                definition.surface,
            )
            continue
        filtered.append(definition)
    return filtered


def _resolve_agent_definitions(
    *,
    include_memory: bool,
    tool_definitions: Sequence[ToolDefinition] | None,
) -> list[ToolDefinition]:
    if tool_definitions is not None:
        return _filter_to_agent_surface(list(tool_definitions))
    return list(
        iter_tool_definitions(surface=Surface.AGENT, include_memory=include_memory)
    )


def build_band_mcp_tool_registrations(
    agent_tools: AgentToolsProtocol,
    *,
    include_memory: bool = False,
    additional_tools: list[CustomToolDef] | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> list[MCPToolRegistration]:
    """Build MCP tool registrations bound to a single, already-live ``AgentTools``.

    For a caller with exactly one room per server instance (e.g. an ACP
    session) -- no room resolution needed, so every ``chat_id`` resolves to
    the same ``agent_tools`` regardless of its value.
    """
    return build_resolved_band_mcp_tool_registrations(
        get_tools=lambda _chat_id: agent_tools,
        include_memory=include_memory,
        additional_tools=additional_tools,
        tool_definitions=tool_definitions,
    )


def build_resolved_band_mcp_tool_registrations(
    *,
    get_tools: RoomToolResolver,
    include_memory: bool = False,
    additional_tools: list[CustomToolDef] | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> list[MCPToolRegistration]:
    """Build MCP registrations that resolve room-scoped tools at call time.

    Uniform room-wrap: every agent tool gets a ``chat_id`` field here,
    regardless of the CLI door's ``AGENT_ROOM_BOUND_TOOL_NAMES``
    classification -- ``chat_id`` is this door's routing key for
    ``AgentTools`` instance selection (e.g. opencode's ``_get_room_tools``),
    so even a CLI-room-less tool like ``band_create_chatroom`` needs one here.
    """
    definitions = _resolve_agent_definitions(
        include_memory=include_memory, tool_definitions=tool_definitions
    )
    resolver = EmbeddedResolver(get_tools=get_tools)
    registrations = [
        build_tool_registration(
            definition,
            extend_with_chat_id(definition.input_model, None),
            resolver=resolver,
            strip_chat_id=True,
        )
        for definition in definitions
    ]
    registrations.extend(
        build_custom_tool_registration(tool_def, room_bound=True)
        for tool_def in additional_tools or []
    )
    validate_unique_tool_names(registrations)
    return registrations


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

    async def start(self) -> None:
        """Start the local MCP server."""
        async with self._lifecycle_lock:
            if self._serve_task and not self._serve_task.done():
                return

            reserved_socket, port = self._reserve_socket()
            # A fresh FastMCP every start(): its session manager is single-use
            # (StreamableHTTPSessionManager.run() raises on a second call), so
            # a start->stop->start cycle needs a brand-new engine, not a
            # restarted one.
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

            self._socket = reserved_socket
            self._port = port
            self._uvicorn_server = uvicorn_server
            self._serve_task = serve_task

            try:
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
            reserved_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reserved_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reserved_socket.bind((self._host, 0))
            port = reserved_socket.getsockname()[1]
            reserved_socket.listen(2048)
            reserved_socket.setblocking(False)
            return reserved_socket, port

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
            reserved_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reserved_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                reserved_socket.bind((self._host, port))
                reserved_socket.listen(2048)
                reserved_socket.setblocking(False)
                return reserved_socket, port
            except OSError as exc:
                last_error = exc
                reserved_socket.close()

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
