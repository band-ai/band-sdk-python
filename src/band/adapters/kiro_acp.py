"""AWS Kiro CLI adapter over ACP.

``KiroACPAdapter`` drives Kiro CLI's ACP server through the generic
:class:`~band.integrations.acp.client_adapter.ACPClientAdapter`. Kiro speaks
standard ACP (``initialize``/``session/new``/``session/prompt``/``session/
load``) plus a handful of experimental ``_kiro.dev/*`` extension methods,
handled by :class:`~band.integrations.acp.client_profiles.KiroACPClientProfile`.

* **stdio** (the only supported transport): spawn ``kiro-cli acp`` as a
  room-owned subprocess on this host. Kiro's ACP mode has no documented
  remote/``--port`` option the way Copilot's does, so there is no TCP branch.

Authentication is ``kiro-cli login`` (AWS Builder ID / Identity Center /
Google / GitHub social OAuth -- browser or device-flow; there is no
non-interactive login flag) or the ``KIRO_API_KEY`` env var, documented for
headless/CI-CD use by the CLI's own embedded help-doc index and read by the
same process-wide auth module the ACP agent reads its environment from (both
confirmed live against ``kiro-cli`` 2.24.0). Pass whatever your chosen method
needs via ``env``; leave it unset to use the CLI's ambient login.

Band tools reach Kiro over MCP the same way as Copilot/Cursor: Kiro's ACP
schema tracks an ACP-injected ``mcpServers`` entry as a distinct, first-class
config source alongside its on-disk ``.kiro/settings/mcp.json`` config
(confirmed by inspecting the installed CLI's own schema), so
``inject_band_tools=True`` (the default, a loopback HTTP/SSE MCP server)
needs no Kiro-specific fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import KiroACPClientProfile
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver

logger = logging.getLogger(__name__)

DEFAULT_KIRO_COMMAND: tuple[str, ...] = ("kiro-cli", "acp")


@dataclass(frozen=True)
class KiroACPAdapterConfig:
    """Runtime configuration for the Kiro CLI ACP backend."""

    command: tuple[str, ...] = DEFAULT_KIRO_COMMAND
    workspace_for_room: WorkspaceResolver | None = None
    # Arbitrary environment for the spawned CLI: KIRO_API_KEY for headless auth,
    # KIRO_HOME to isolate login/session state, or any other Kiro CLI env var.
    # Unset uses the CLI's ambient login.
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None


class KiroACPAdapter(ACPClientAdapter):
    """Band adapter for AWS Kiro CLI over ACP.

    A thin specialization of :class:`ACPClientAdapter` that presets the Kiro
    command and the Kiro-specific extension profile.
    """

    def __init__(
        self,
        config: KiroACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or KiroACPAdapterConfig()

        super().__init__(
            command=list(config.command),
            env=config.env,
            workspace_for_room=config.workspace_for_room,
            mcp_servers=config.mcp_servers,
            additional_tools=additional_tools,
            inject_band_tools=config.inject_band_tools,
            custom_section=config.custom_section,
            profile=KiroACPClientProfile(),
            resolve_session_config=config.resolve_session_config,
            **features,
        )


__all__ = [
    "DEFAULT_KIRO_COMMAND",
    "KiroACPAdapter",
    "KiroACPAdapterConfig",
]
