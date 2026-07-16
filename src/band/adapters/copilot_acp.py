"""GitHub Copilot CLI adapter over ACP.

``CopilotACPAdapter`` drives the GitHub Copilot CLI's ACP server through the
generic :class:`~band.integrations.acp.client_adapter.ACPClientAdapter`. Copilot
speaks vanilla ACP (it emits no ``copilot/*`` extension methods), so no custom
client profile is needed — the default no-op profile suffices.

Two transports, selected by the config:

* **stdio** (default): spawn ``copilot --acp`` as a subprocess on this host.
* **TCP**: connect to an already-running ``copilot --acp --port N`` (e.g. Copilot
  in a container); set ``host`` + ``port``.

Authentication is flexible — the CLI resolves credentials in this order:
``COPILOT_GITHUB_TOKEN`` > ``GH_TOKEN`` > ``GITHUB_TOKEN`` (env token; the
documented path for headless/containers), then a stored ``copilot login`` (OS
keychain, or ``<COPILOT_HOME>/config.json``, default ``~/.copilot``), then an
authenticated ``gh`` CLI, then BYOK (own LLM keys — no GitHub token needed).
For the **stdio** transport, pass whatever your chosen method needs via ``env``
(``github_token`` is a convenience that sets ``GITHUB_TOKEN``); leave both unset
to use the CLI's ambient login. Over **TCP** the already-running server carries
its own environment, so ``env`` / ``github_token`` are ignored.

Band tools reach Copilot over MCP. When co-located with the SDK, keep
``inject_band_tools=True`` (a loopback HTTP/SSE MCP server). For a remote Copilot
that cannot reach the SDK host's loopback, set ``inject_band_tools=False`` and
pass an explicit reachable ``mcp_servers`` entry instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from band.core.types import AdapterFeatures
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")


@dataclass(frozen=True)
class CopilotACPAdapterConfig:
    """Runtime configuration for the Copilot CLI ACP backend.

    ``command`` (stdio) and ``host``/``port`` (TCP) are mutually exclusive; the
    default is a stdio spawn of ``copilot --acp``.
    """

    command: tuple[str, ...] = DEFAULT_COPILOT_COMMAND
    host: str | None = None
    port: int | None = None
    cwd: str | None = None
    github_token: str | None = None
    # Arbitrary environment for the spawned CLI (stdio): any auth method Copilot
    # supports — COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN, BYOK provider keys, etc.
    # Merged over github_token; ignored for TCP (the server owns its environment).
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    rest_url: str | None = None
    mcp_servers: list[dict[str, Any]] | None = None


class CopilotACPAdapter(ACPClientAdapter):
    """Band adapter for the GitHub Copilot CLI over ACP.

    A thin specialization of :class:`ACPClientAdapter` that presets the Copilot
    command, no-op profile (default), and ``GITHUB_TOKEN`` environment, and
    selects the stdio or TCP transport from the config.
    """

    def __init__(
        self,
        config: CopilotACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        config = config or CopilotACPAdapterConfig()
        use_tcp = config.host is not None or config.port is not None

        # command (stdio) and host/port (TCP) are mutually exclusive. Since command
        # has a default, a caller who sets BOTH a non-default command and host/port
        # is misconfigured — fail loudly rather than silently dropping the command.
        if use_tcp and tuple(config.command) != DEFAULT_COPILOT_COMMAND:
            raise ValueError("set either command (stdio) or host/port (TCP), not both")

        # Over TCP the already-running server owns its environment, so any auth we
        # were handed here is dropped — warn rather than let a caller believe auth
        # is configured (symmetric with the loud command+TCP error above).
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

        common: dict[str, Any] = {
            "env": env,
            "cwd": config.cwd,
            "mcp_servers": config.mcp_servers,
            "additional_tools": additional_tools,
            "rest_url": config.rest_url,
            "inject_band_tools": config.inject_band_tools,
            "custom_section": config.custom_section,
            "features": features,
        }

        if use_tcp:
            super().__init__(host=config.host, port=config.port, **common)
        else:
            super().__init__(command=list(config.command), **common)


__all__ = [
    "CopilotACPAdapter",
    "CopilotACPAdapterConfig",
    "DEFAULT_COPILOT_COMMAND",
]
