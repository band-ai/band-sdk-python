"""The shared Band MCP backend: one local MCP server per adapter, owned by a
``SharedBandMCPBackend`` that starts, replaces and stops it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from band.core.exceptions import CleanupIncompleteError
from band.core.retention.cleanup import cleanup_deadline, finish_by, spawn_owned
from band.core.retention.config import RoomRetentionConfig
from band.integrations.mcp.engine import (
    RoomToolResolver,
    build_resolved_band_mcp_tool_registrations,
)
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HOST,
    LOCAL_MCP_PORT_MAX,
    LOCAL_MCP_PORT_MIN,
    LocalMCPServer,
)
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name
from band.runtime.tools import BAND_MCP_SERVER_NAME, ToolDefinition

logger = logging.getLogger(__name__)

#: The cleanup steps ``close_after`` reports, as cleanup reports name them.
ROOMS_CLOSE = "Room shutdown"
MCP_FINAL_CLOSE = "Band MCP backend final close"


class BandMCPTransport(StrEnum):
    """The wire transports every Band MCP server serves."""

    HTTP = "http"
    SSE = "sse"


class BandMCPBackendStoppedError(RuntimeError):
    """A ``SharedBandMCPBackend`` was asked for its backend after a final close."""


@dataclass(frozen=True)
class BandMCPBackend:
    """A Band MCP server (both transports) and the tool names it exposes.

    Never restarted: its URLs stay fixed for its whole life, so a URL handed
    out names exactly one server.
    """

    allowed_tools: list[str]
    local_server: LocalMCPServer

    @property
    def is_running(self) -> bool:
        """False once the backing local server has crashed or stopped."""
        return self.local_server.is_running

    def endpoint(self, transport: BandMCPTransport, room_id: str | None = None) -> str:
        """Return the URL a consumer dials: its room's, or the multi-room one.

        Raises ``ValueError`` when passing (or omitting) ``room_id`` doesn't
        match how the backend was created (``room_bound``).
        """
        server = self.local_server
        match transport:
            case BandMCPTransport.HTTP:
                return (
                    server.http_url
                    if room_id is None
                    else server.room_http_url(room_id)
                )
            case BandMCPTransport.SSE:
                return (
                    server.sse_url if room_id is None else server.room_sse_url(room_id)
                )

    async def stop(self) -> None:
        """Stop the backing local server."""
        await self.local_server.stop()


@dataclass(frozen=True)
class BandMCPBackendSettings:
    """What an adapter needs from its Band MCP backend.

    A ``room_bound`` backend serves one endpoint per room
    (``endpoint(transport, room_id)``) whose tools take their room from the
    path and advertise no ``chat_id``; otherwise one multi-room endpoint
    routes by a required ``chat_id`` argument. ``host`` sets the bind
    interface; see ``LocalMCPServer`` for the non-loopback caveat.
    ``port_min=0`` requests an OS-assigned ephemeral port; ``avoid_port`` has
    no effect there, so a replacement may get its dead predecessor's URL back.
    """

    tool_definitions: Sequence[ToolDefinition]
    get_tools: RoomToolResolver
    additional_tools: Sequence[CustomToolDef] = ()
    room_bound: bool = False
    host: str = LOCAL_MCP_HOST
    port_min: int = LOCAL_MCP_PORT_MIN
    port_max: int = LOCAL_MCP_PORT_MAX


def _build_allowed_tools(
    tool_definitions: Sequence[ToolDefinition],
    additional_tools: list[CustomToolDef],
) -> list[str]:
    allowed_tools = [f"mcp__band__{definition.name}" for definition in tool_definitions]
    allowed_tools.extend(
        f"mcp__band__{get_custom_tool_name(input_model)}"
        for input_model, _ in additional_tools
    )
    return allowed_tools


async def create_band_mcp_backend(
    settings: BandMCPBackendSettings, *, avoid_port: int | None = None
) -> BandMCPBackend:
    """Start a Band MCP server, serving both transports, as ``settings`` describe."""
    additional_tools = list(settings.additional_tools)
    local_server = LocalMCPServer(
        name=BAND_MCP_SERVER_NAME,
        tool_registrations=build_resolved_band_mcp_tool_registrations(
            get_tools=settings.get_tools,
            additional_tools=additional_tools,
            tool_definitions=settings.tool_definitions,
            room_from_connection=settings.room_bound,
        ),
        host=settings.host,
        port_min=settings.port_min,
        port_max=settings.port_max,
        room_bound=settings.room_bound,
        avoid_port=avoid_port,
    )
    await local_server.start()
    backend = BandMCPBackend(
        allowed_tools=_build_allowed_tools(settings.tool_definitions, additional_tools),
        local_server=local_server,
    )
    logger.info(
        "Band MCP server started with %s tools (%s custom)",
        len(backend.allowed_tools),
        len(additional_tools),
    )
    return backend


class SharedBandMCPBackend:
    """One adapter's Band MCP backend: started on first use, replaced when its
    server dies, refused once closed for good.

    An async context manager for block-scoped use. Adapters, whose lifetime
    spans ``on_started`` to ``cleanup_all``, call ``ensure()``/``close()``
    directly -- the same idiom as ``LocalMCPServer``'s ``start()``/``stop()``.
    ``settings`` is read at each start, replacements included, since
    capabilities (and with them the tool definitions) are only settled when
    the agent starts.
    """

    def __init__(self, settings: Callable[[], BandMCPBackendSettings]) -> None:
        self._settings = settings
        self._backend: BandMCPBackend | None = None
        self._closed = False
        # A final close owns its backend until the stop is confirmed.
        self._closing: BandMCPBackend | None = None
        self._stopping: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close(final=True)

    @property
    def current(self) -> BandMCPBackend | None:
        """The servable backend, or ``None`` when none is.

        ``None`` before the first start, after a non-final detach, and while
        a final close still owns the previous backend until that stop succeeds.
        """
        return self._backend

    async def ensure(self) -> BandMCPBackend:
        """The running backend: started on first use, replaced if its server died."""
        async with self._lock:
            if self._closed:
                raise BandMCPBackendStoppedError()
            if self._backend is None or not self._backend.is_running:
                self._backend = await self._start(replacing=self._backend)
            return self._backend

    async def detach(self, *, final: bool) -> BandMCPBackend | None:
        """Hand the backend over; from then on the caller owns stopping it.

        ``final`` refuses every later ``ensure()`` until ``reopen()``, so a
        message parked on the lock through shutdown can't start a server
        nothing would stop. A non-final detach never lifts that refusal.
        """
        async with self._lock:
            if final:
                self._closed = True
            backend, self._backend = self._backend, None
            return backend

    async def close(self, *, final: bool) -> None:
        """Stop the backend outside the lock, so a slow stop never holds up
        the next ``ensure()``.

        ``final=False`` detaches the backend and the caller waits on that
        stop. A final close keeps the backend and its stop task until the
        stop succeeds: a cancelled caller leaves the stop running, a retry
        joins it, and a retry after a failure stops that same backend again.
        """
        if not final:
            if (backend := await self.detach(final=False)) is not None:
                await backend.stop()
            return
        if (stopping := await self._final_stop()) is not None:
            await asyncio.shield(stopping)

    async def close_after(
        self, rooms_closed: Awaitable[None], *, retention: RoomRetentionConfig
    ) -> None:
        """Close for good once the rooms whose processes dial this server have
        closed, both within one ``retention.transition_timeout_s`` deadline.

        Raises:
            CleanupIncompleteError: A step outlived the deadline; it stays
                owned for a retry.
        """
        deadline = cleanup_deadline(retention)
        await finish_by(deadline, rooms_closed, what=ROOMS_CLOSE)
        await finish_by(deadline, self.close(final=True), what=MCP_FINAL_CLOSE)

    async def _final_stop(self) -> asyncio.Task[None] | None:
        async with self._lock:
            self._closed = True
            if (closing := self._claim_closing()) is None:
                return None
            if self._stopping is None or self._stopping.done():
                self._stopping = spawn_owned(
                    self._stop_closing(closing), label=MCP_FINAL_CLOSE
                )
            return self._stopping

    def _claim_closing(self) -> BandMCPBackend | None:
        """A pending final close keeps its backend; otherwise it takes the
        current one."""
        if self._closing is None:
            self._closing, self._backend = self._backend, None
        return self._closing

    async def _stop_closing(self, backend: BandMCPBackend) -> None:
        await backend.stop()
        self._closing = None

    async def reopen(self) -> None:
        """Accept ``ensure()`` again after a final close (an agent restarting).

        Raises:
            CleanupIncompleteError: The final close has not stopped its
                backend yet; ``close(final=True)`` retries it.
        """
        async with self._lock:
            if self._closing is not None:
                raise CleanupIncompleteError(f"{MCP_FINAL_CLOSE} is still pending")
            self._closed = False

    async def _start(self, *, replacing: BandMCPBackend | None) -> BandMCPBackend:
        """A new backend; a dead one it replaces stays held until this succeeds,
        so a failed start leaves the next ``ensure()`` to retry."""
        if replacing is not None:
            logger.warning(
                "Band MCP server on port %s is not running; starting a replacement",
                replacing.local_server.port,
            )
            await replacing.stop()
        return await create_band_mcp_backend(
            self._settings(),
            avoid_port=replacing.local_server.port if replacing else None,
        )
