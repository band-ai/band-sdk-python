"""The Band MCP tool path for the Letta adapter.

``LettaMCPBridge`` owns everything between the adapter and the Letta server's
tool surface: the self-hosted in-process ``LocalMCPServer``, its registration
in Letta (lookup/create with conflict recovery), tool discovery, and the
resolved send-tool names.  It lives apart from the adapter because this is
where the Letta-server behavioral quirks are handled — soft-deleted
registration names, the deregister-then-die teardown wedge, create/retry
races — see the method docstrings.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

from band.core.protocols import AgentToolsProtocol
from band.integrations.letta.config import LettaMCPConfig
from band.integrations.letta.prompts import (
    SEND_EVENT_TOOL_NAMES,
    SEND_MESSAGE_TOOL_NAMES,
)
from band.integrations.mcp.backends import (
    BandMCPBackend,
    create_band_mcp_backend,
)
from band.runtime.mcp_server import LOCAL_MCP_HTTP_PATH, LOCAL_MCP_SSE_PATH
from band.runtime.tools import ToolDefinition

logger = logging.getLogger(__name__)

# Transport -> the LocalMCPServer mount path serving it.
_MCP_URL_PATHS: dict[str, str] = {
    "sse": LOCAL_MCP_SSE_PATH,
    "streamable_http": LOCAL_MCP_HTTP_PATH,
}


async def bounded_teardown(
    operation: Awaitable[Any], *, timeout_s: float, action: str
) -> bool:
    """Run a Letta teardown operation without letting it fail or wedge shutdown."""
    try:
        await asyncio.wait_for(operation, timeout=timeout_s)
        return True
    except TimeoutError:
        logger.warning(
            "Timed out after %.2fs while trying to %s; continuing teardown",
            timeout_s,
            action,
        )
    except Exception as e:
        logger.warning("Failed to %s during Letta teardown: %s", action, e)
    return False


class LettaMCPBridge:
    """One adapter instance's Band MCP tool path.

    The bridge talks to the adapter through exactly two seams: the room-scoped
    ``get_tools`` resolver it hands the self-hosted server, and the resolved
    ``send_message_tool``/``send_event_tool`` names the adapter's prompt and
    relay detection read back.  The Letta client is passed per call — the
    adapter owns its lifecycle.
    """

    def __init__(
        self,
        config: LettaMCPConfig,
        *,
        tool_definitions: Sequence[ToolDefinition],
        get_tools: Callable[[str], AgentToolsProtocol | None],
        teardown_timeout_s: float,
    ) -> None:
        self._config = config
        self._tool_definitions = list(tool_definitions)
        self._get_tools = get_tools
        self._teardown_timeout_s = teardown_timeout_s

        # Self-hosted MCP backend (None in external mode / not yet started).
        self.backend: BandMCPBackend | None = None
        # Registration id and tool ids in Letta (populated by ensure_ready).
        self.server_id: str | None = None
        self.tool_ids: list[str] = []
        # Send-tool names resolved from the registered server's discovered
        # tools; the first known alias is the pre-discovery fallback.
        self.send_message_tool: str = SEND_MESSAGE_TOOL_NAMES[0]
        self.send_event_tool: str = SEND_EVENT_TOOL_NAMES[0]

    @property
    def ready(self) -> bool:
        """Whether the tool path is registered and discovered."""
        return self.server_id is not None

    @property
    def silent_reporting_tools(self) -> frozenset[str]:
        """Tools not reported as tool_call/tool_result execution events.

        Their execution already produces visible output (a message or event)
        on the platform, so reporting them would be duplicate noise.
        """
        return frozenset({self.send_message_tool, self.send_event_tool})

    async def ensure_ready(self, client: Any) -> None:
        """Make the Band MCP tool path available to the Letta server.

        External mode registers the configured server once.  Self-host mode
        starts the in-process ``LocalMCPServer`` (reusing a still-running one
        after ``release``) and registers its advertised URL under a fresh
        unique name (see ``LettaMCPConfig.server_name``).  Idempotent once
        ready.
        """
        if self.ready:
            return

        if self._config.mode == "external":
            await self.register(
                client,
                server_name=self._config.server_name or "band",
                server_url=self._config.server_url,
            )
            return

        # The backend is kept (never stopped) on failure: a half-committed
        # registration may still point at it, and Letta only tolerates a
        # registration whose server stays alive (see release). The retry on
        # the next message reuses it under a fresh name.
        backend = await self._start_backend()
        local_server = backend.local_server
        if local_server is None:
            raise RuntimeError("Band MCP backend has no local server to register")
        await self.register(
            client,
            server_name=self._config.server_name or f"band-{uuid4().hex[:8]}",
            server_url=self.advertised_url(local_server.port),
        )

    async def register(self, client: Any, *, server_name: str, server_url: str) -> None:
        """Register a Band MCP server with Letta and discover its tools.

        Uses lookup-or-create to handle adapter restarts where the MCP server
        name is already registered in Letta.  An adopted registration must point
        at the same ``server_url`` — otherwise a crash-restart can wire agents
        to a dead port, or two instances can cross-delete each other's row.
        """
        try:
            effective_name = server_name
            server = await self._find(client, effective_name)
            if server is not None:
                existing_url = self._registered_url(server)
                if existing_url and existing_url != server_url:
                    logger.warning(
                        "MCP registration %r points at %s, expected %s",
                        effective_name,
                        existing_url,
                        server_url,
                    )
                    if (
                        self._config.mode == "self_host"
                        and self._config.server_name is None
                    ):
                        effective_name = f"band-{uuid4().hex[:8]}"
                        server = None
                    else:
                        raise RuntimeError(
                            f"MCP registration {effective_name!r} points at "
                            f"{existing_url!r} but this adapter advertises "
                            f"{server_url!r}. Remove the stale registration "
                            f"from Letta or use an ephemeral self-hosted name "
                            f"(omit mcp.server_name)."
                        )
                elif server is not None:
                    logger.info(
                        "Found existing MCP server %r (id=%s)",
                        effective_name,
                        server.id,
                    )
            if server is None:
                server = await self._create(client, effective_name, server_url)

            server_id = server.id

            # Discover available tools from the MCP server
            tools = await client.mcp_servers.tools.list(mcp_server_id=server_id)
            self.tool_ids = [t.id for t in tools if getattr(t, "id", None)]
            tool_names = [t.name for t in tools]
            self.resolve_send_tools(tool_names)
            # Ready only once discovery succeeded — a half-registered server
            # must not short-circuit the next ensure_ready into running
            # agents with no tools.
            self.server_id = server_id
            logger.info("Discovered %d MCP tools: %s", len(self.tool_ids), tool_names)
        except Exception as e:
            logger.error("Failed to register MCP server: %s", e)
            raise RuntimeError(
                f"MCP server registration failed. Ensure the Band MCP server "
                f"is reachable by Letta at {server_url}: {e}"
            ) from e

    @property
    def registration_rotates_on_release(self) -> bool:
        """Whether ``release`` deregisters from Letta and mints new tool ids.

        True only for self-host mode with an auto-generated registration name
        (``server_name`` unset).  Fixed names and external mode keep the Letta
        row across adapter restarts, so tool ids stay stable.
        """
        return self._config.mode == "self_host" and self._config.server_name is None

    async def release(self, client: Any) -> None:
        """Release the local MCP tool-path cache; deregister only when safe.

        **External mode** — no-op.  The registration names a long-lived server
        this process does not own (often the shared default ``"band"``).
        Deleting it would strip tools from other live agents; clearing cached
        ids would force pointless re-discovery on restart.

        **Self-host, fixed ``server_name``** — clear ``server_id`` / ``tool_ids``
        only.  The Letta row is kept: deregistering a fixed name soft-deletes
        it permanently.  The next ``ensure_ready`` re-adopts by name after
        verifying the URL.  Because the in-process server uses an OS-assigned
        port, a process restart usually changes the URL — see
        ``LettaMCPConfig.server_name``.

        **Self-host, ephemeral name** (``server_name`` unset) — deregister the
        Letta row, then clear cache.  The in-process server keeps running until
        process exit (Letta closes its MCP session asynchronously after delete;
        stopping the server around that close wedges Letta's serial sync worker).
        The next ``ensure_ready`` registers under a fresh ``band-{suffix}`` name.
        State is cleared before any await so a concurrent message can re-register.
        """
        if self._config.mode == "external":
            return
        server_id = self.server_id
        self.server_id = None
        self.tool_ids = []
        if client and server_id and self.registration_rotates_on_release:
            ok = await bounded_teardown(
                client.mcp_servers.delete(server_id),
                timeout_s=self._teardown_timeout_s,
                action=f"deregister MCP server {server_id}",
            )
            if ok:
                logger.debug("Deregistered MCP server %s from Letta", server_id)

    def resolve_send_tools(self, tool_names: list[str]) -> None:
        """Derive the send/event tool names from the server's discovered tools.

        Keeps the enforcement prompt, silent-reporting set, and auto-relay
        detection aligned with whatever Band MCP surface is registered instead
        of hardcoding one surface's names.
        """
        names = set(tool_names)
        self.send_message_tool = next(
            (name for name in SEND_MESSAGE_TOOL_NAMES if name in names),
            SEND_MESSAGE_TOOL_NAMES[0],
        )
        self.send_event_tool = next(
            (name for name in SEND_EVENT_TOOL_NAMES if name in names),
            SEND_EVENT_TOOL_NAMES[0],
        )
        if self.send_message_tool not in names:
            logger.warning(
                "Registered MCP server exposes no known send-message tool "
                "(looked for %s in %s); replies will depend on auto-relay",
                list(SEND_MESSAGE_TOOL_NAMES),
                tool_names,
            )

    def advertised_url(self, port: int) -> str:
        """The URL Letta uses to reach the self-hosted MCP server."""
        if self._config.advertised_host:
            host = self._config.advertised_host
        elif self._config.bind_host in ("0.0.0.0", "::"):
            # 0.0.0.0 is a listen wildcard, not routable back to this host.
            host = "127.0.0.1"
        else:
            host = self._config.bind_host
        return f"http://{host}:{port}{_MCP_URL_PATHS[self._config.transport]}"

    @staticmethod
    def _registered_url(server: Any) -> str | None:
        """The server URL stored on a Letta MCP registration, if any."""
        config = getattr(server, "config", None)
        if config is not None:
            url = getattr(config, "server_url", None)
            if url:
                return str(url)
            if isinstance(config, dict) and config.get("server_url"):
                return str(config["server_url"])
        url = getattr(server, "server_url", None)
        return str(url) if url else None

    async def _start_backend(self) -> BandMCPBackend:
        """Start the in-process Band MCP server (self_host mode)."""
        if self.backend is not None:
            return self.backend

        # Ephemeral OS-assigned port (never reused): the Letta server dials
        # back across a network proxy (docker host-gateway), and re-binding a
        # just-freed scanned port can leave that hop stalled on stale state.
        backend = await create_band_mcp_backend(
            kind="sse" if self._config.transport == "sse" else "http",
            tool_definitions=self._tool_definitions,
            get_tools=self._get_tools,
            host=self._config.bind_host,
            port_min=0,
            port_max=0,
        )
        self.backend = backend
        logger.info(
            "Self-hosted Band MCP server started with %d tools",
            len(backend.allowed_tools),
        )
        return backend

    async def _find(self, client: Any, server_name: str) -> Any | None:
        """The registered MCP server named ``server_name``, or None."""
        servers = await client.mcp_servers.list()
        return next(
            (
                s
                for s in servers
                if getattr(s, "server_name", None) == server_name
                or getattr(s, "name", None) == server_name
            ),
            None,
        )

    async def _create(self, client: Any, server_name: str, server_url: str) -> Any:
        """Create the MCP server registration, riding out a create/retry race.

        Letta's create synchronously syncs tools from the server and can
        exceed the HTTP client's timeout under load; the client then retries
        and conflicts with its own committed first attempt.  A post-conflict
        lookup recovers the committed registration instead of failing the
        turn.
        """
        try:
            server = await client.mcp_servers.create(
                server_name=server_name,
                config={
                    "mcp_server_type": self._config.transport,
                    "server_url": server_url,
                },
            )
        except Exception as create_error:
            server = await self._find(client, server_name)
            if server is None:
                raise create_error
            logger.info(
                "MCP server %r create conflicted; recovered committed "
                "registration (id=%s)",
                server_name,
                server.id,
            )
            return server
        logger.info(
            "Registered MCP server %r (id=%s) at %s",
            server_name,
            server.id,
            server_url,
        )
        return server
