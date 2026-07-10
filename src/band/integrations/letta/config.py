"""Configuration for the Letta adapter."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Literal

from band.core.exceptions import BandConfigError
from band.runtime.mcp_server import LOCAL_MCP_HOST

MCPTransport = Literal["sse", "streamable_http"]


@dataclass
class LettaMCPConfig:
    """How the Letta server reaches the Band MCP tools.

    Two modes:

    - ``self_host`` (default): the adapter starts an in-process
      ``LocalMCPServer`` and registers it with Letta.  Tool calls execute
      in-process as the adapter's own agent, resolved per room at call time —
      no separate server, no extra credentials.  ``bind_host`` sets the
      listening interface; a non-loopback bind (``"0.0.0.0"``) exposes the
      agent's tools to the local network, so only opt in on an isolated host —
      it is needed when the Letta server runs in a container and reaches back
      via ``host.docker.internal``, which is what ``advertised_host`` names.
    - ``external``: register a separately-run Band MCP server (e.g. band-mcp)
      at ``server_url``.  Required for Letta Cloud, which cannot reach a
      laptop-local server.
    """

    mode: Literal["self_host", "external"] = "self_host"
    # external mode: URL of the running Band MCP server (ignored when
    # self-hosted — the adapter advertises its own server's URL).
    server_url: str = "http://localhost:8002/sse"
    # Registration name in Letta. None resolves to "band" in external mode and
    # to a fresh unique "band-{suffix}" per registration when self-hosted:
    # Letta soft-deletes registrations, so a deregistered name can never be
    # reused (unique-constraint conflict), and fresh names also keep concurrent
    # adapter instances on one Letta server from cross-wiring their tools.
    #
    # Setting a fixed name in self-host mode skips deregistration on shutdown
    # (so the name is not poisoned) but ties the row to one server URL. Because
    # the in-process MCP server binds an OS-assigned port each start, a process
    # restart usually changes the URL — ``register()`` then fails until the stale
    # row is removed from Letta manually. Prefer leaving this None (ephemeral
    # names) unless the advertised URL is stable across restarts.
    server_name: str | None = None
    # self_host mode: interface the local server binds.
    bind_host: str = LOCAL_MCP_HOST
    # self_host mode: hostname Letta uses to reach the local server (defaults
    # to bind_host). Set "host.docker.internal" for a dockerized Letta.
    advertised_host: str | None = None
    transport: MCPTransport = "sse"


@dataclass
class LettaAdapterConfig:
    """Configuration for the Letta adapter.

    Works with both Letta Cloud and self-hosted Letta.  For Letta Cloud
    (the default), provide a ``provider_key`` and optionally set ``project``
    to scope to a specific project.  For self-hosted, set ``base_url``
    to your server (e.g. ``"http://localhost:8283"``) — no ``provider_key``
    is required.

    Platform tools reach the Letta server over MCP; see ``LettaMCPConfig``
    (``mcp``) for the self-hosted default and the external band-mcp mode.
    ``embedding`` is required by Letta's Docker server on agent create
    (e.g. ``"openai/text-embedding-3-small"``); Letta Cloud picks a default
    when omitted.
    """

    agent_id: str | None = None
    model: str | None = None
    provider_key: str | None = None  # Required for Letta Cloud; omit for self-hosted
    api_key: str | None = None  # deprecated, use provider_key
    base_url: str = "https://api.letta.com"
    custom_section: str = ""
    include_base_instructions: bool = True
    enable_execution_reporting: bool = False
    enable_task_events: bool = True
    enable_memory_tools: bool = False
    persona: str | None = None
    turn_timeout_s: float = 300.0
    memory_blocks: list[dict[str, str]] = field(default_factory=list)
    summary_max_length: int = 150

    # Letta Cloud project scoping (ignored for self-hosted)
    project: str | None = None

    # Embedding model passed on agent create. Letta's Docker server requires
    # one; Cloud picks its own default when None.
    embedding: str | None = None

    # MCP tool path configuration (see LettaMCPConfig).
    mcp: LettaMCPConfig = field(default_factory=LettaMCPConfig)
    # Deprecated compatibility shims for the pre-nested MCP config API.
    mcp_server_url: str | None = field(default=None, kw_only=True)
    mcp_server_name: str | None = field(default=None, kw_only=True)

    # Relay the agent's plain assistant text into the room when it did not
    # call the MCP send tool. Keeps the agent responsive when the model skips
    # tools, but can mask a dead MCP tool path — disable to make an unused
    # tool path fail loudly (an error event) instead.
    auto_relay: bool = True

    # Delete the room's Letta agent on cleanup. Off by default: persisted
    # agents are what makes resume-by-id work across restarts. Opt in as
    # hygiene for long-lived self-hosted servers (per_room mode only — the
    # shared agent outlives any single room).
    delete_agents_on_cleanup: bool = False
    # Ask Letta to consolidate room context into memory on cleanup. This is
    # useful for long-lived agents but is another live Letta/LLM call during
    # teardown, so CI can disable it without changing turn behavior.
    consolidate_memory_on_cleanup: bool = True
    # Upper bound for best-effort Letta teardown calls. Letta server cleanup
    # paths can stall when closing MCP sessions; teardown must warn and move on.
    teardown_timeout_s: float = 10.0

    # Operating mode: per_room creates one Letta agent per room,
    # shared uses one agent with per-room Conversations for isolation.
    mode: Literal["per_room", "shared"] = "per_room"

    def __post_init__(self) -> None:
        if self.api_key is not None:
            warnings.warn(
                "api_key is deprecated on LettaAdapterConfig, use provider_key instead",
                DeprecationWarning,
                stacklevel=2,
            )
            if self.provider_key is not None:
                raise BandConfigError("Cannot pass both provider_key and api_key")
            self.provider_key = self.api_key
            self.api_key = None

        if self.mcp_server_url is not None or self.mcp_server_name is not None:
            warnings.warn(
                "mcp_server_url and mcp_server_name are deprecated on "
                "LettaAdapterConfig, use mcp=LettaMCPConfig(...) instead",
                DeprecationWarning,
                stacklevel=2,
            )
            server_url = (
                self.mcp_server_url
                if self.mcp_server_url is not None
                else self.mcp.server_url
            )
            server_name = (
                self.mcp_server_name
                if self.mcp_server_name is not None
                else self.mcp.server_name
            )
            self.mcp = LettaMCPConfig(
                mode="external",
                server_url=server_url,
                server_name=server_name,
                transport=self.mcp.transport,
            )
            self.mcp_server_url = None
            self.mcp_server_name = None
