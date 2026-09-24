"""GitHub Copilot CLI adapter over ACP.

``CopilotACPAdapter`` drives the GitHub Copilot CLI's ACP server through the
generic :class:`~band.integrations.acp.client_adapter.ACPClientAdapter`. Copilot
speaks vanilla ACP (it emits no ``copilot/*`` extension methods), so no custom
client profile is needed — the default no-op profile suffices.

* **stdio** (default and only supported transport): spawn ``copilot --acp`` as a
  room-owned subprocess on this host.

Authentication is flexible — the CLI resolves credentials in this order:
``COPILOT_GITHUB_TOKEN`` > ``GH_TOKEN`` > ``GITHUB_TOKEN`` (env token; the
documented path for headless/containers), then a stored ``copilot login`` (OS
keychain, or ``<COPILOT_HOME>/config.json``, default ``~/.copilot``), then an
authenticated ``gh`` CLI, then BYOK (own LLM keys — no GitHub token needed).
For the **stdio** transport, pass whatever your chosen method needs via ``env``
(``github_token`` is a convenience that sets ``GITHUB_TOKEN``); leave both unset
to use the CLI's ambient login.

Band tools reach Copilot over MCP. When co-located with the SDK, keep
``inject_band_tools=True`` (a loopback HTTP/SSE MCP server). For a remote Copilot
that cannot reach the SDK host's loopback, set ``inject_band_tools=False`` and
pass an explicit reachable ``mcp_servers`` entry instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver, create_room_workspace_resolver

logger = logging.getLogger(__name__)

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")


@dataclass(frozen=True)
class CopilotACPAdapterConfig:
    """Runtime configuration for the Copilot CLI ACP backend.

    ``command`` selects the room-owned stdio process. ``cwd`` is a compatibility
    alias for a workspace root; prefer ``workspace_for_room`` for new code.
    """

    command: tuple[str, ...] = DEFAULT_COPILOT_COMMAND
    host: str | None = None
    port: int | None = None
    cwd: str | None = None
    workspace_for_room: WorkspaceResolver | None = None
    github_token: str | None = None
    # Arbitrary environment for the spawned CLI (stdio): any auth method Copilot
    # supports — COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN, BYOK provider keys, etc.
    # Merged over github_token; ignored for TCP (the server owns its environment).
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None


class CopilotACPAdapter(ACPClientAdapter):
    """Band adapter for the GitHub Copilot CLI over ACP.

    A thin specialization of :class:`ACPClientAdapter` that presets the Copilot
    command, no-op profile (default), and ``GITHUB_TOKEN`` environment.
    """

    def __init__(
        self,
        config: CopilotACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or CopilotACPAdapterConfig()
        use_tcp = config.host is not None or config.port is not None

        # command (stdio) and host/port (TCP) are mutually exclusive. Since command
        # has a default, a caller who sets BOTH a non-default command and host/port
        # is misconfigured — fail loudly rather than silently dropping the command.
        if use_tcp and tuple(config.command) != DEFAULT_COPILOT_COMMAND:
            raise ValueError("set either command (stdio) or host/port (TCP), not both")

        # A rejected remote transport owns its environment, so any auth supplied
        # with it is ignored after this warning.
        if use_tcp and (config.github_token or config.env):
            logger.warning(
                "github_token/env are ignored over TCP: the already-running "
                "copilot --acp server owns its own environment; configure auth on "
                "that server instead."
            )

        # Auth/env for the spawned CLI (stdio only; a TCP server owns its own env).
        # Pass any method's env via config.env; github_token is a convenience for
        # GITHUB_TOKEN (an explicit env entry wins). None => the CLI's ambient login.
        env: dict[str, str] | None = None
        if not use_tcp:
            env = dict(config.env or {})
            if config.github_token:
                env.setdefault("GITHUB_TOKEN", config.github_token)
            env = env or None

        workspace_for_room = config.workspace_for_room
        if config.cwd is not None:
            if workspace_for_room is not None:
                raise ValueError("set either cwd or workspace_for_room, not both")
            workspace_for_room = create_room_workspace_resolver(config.cwd)

        common: dict[str, Any] = {
            "env": env,
            "workspace_for_room": workspace_for_room,
            "mcp_servers": config.mcp_servers,
            "additional_tools": additional_tools,
            "inject_band_tools": config.inject_band_tools,
            "custom_section": config.custom_section,
            "resolve_session_config": config.resolve_session_config,
        }

        if use_tcp:
            super().__init__(host=config.host, port=config.port, **common, **features)
        else:
            super().__init__(command=list(config.command), **common, **features)


__all__ = [
    "DEFAULT_COPILOT_COMMAND",
    "CopilotACPAdapter",
    "CopilotACPAdapterConfig",
]
