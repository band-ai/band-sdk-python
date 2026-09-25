"""AWS Kiro CLI adapter over ACP.

``KiroACPAdapter`` spawns ``kiro-cli acp`` (stdio only -- Kiro documents no
remote/``--port`` mode) through the generic
:class:`~band.integrations.acp.client_adapter.ACPClientAdapter`, with
:class:`~band.integrations.acp.client_profiles.KiroACPClientProfile` handling
Kiro's ``_kiro.dev/*`` extensions.

Auth is the CLI's ambient ``kiro-cli login`` or ``KIRO_API_KEY`` passed via
``env`` for headless use. Band tools reach Kiro through the ACP-injected
``mcpServers`` entry, like Copilot/Cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import ACPClientAdapter, PermissionResolver
from band.integrations.acp.client_profiles import KiroACPClientProfile
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver

DEFAULT_KIRO_COMMAND: tuple[str, ...] = ("kiro-cli", "acp")


@dataclass(frozen=True)
class KiroACPAdapterConfig:
    """Runtime configuration for the Kiro CLI ACP backend."""

    command: tuple[str, ...] = DEFAULT_KIRO_COMMAND
    workspace_for_room: WorkspaceResolver | None = None
    # e.g. KIRO_API_KEY for headless auth or KIRO_HOME to isolate state.
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    resolve_permission: PermissionResolver | None = None


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
            resolve_permission=config.resolve_permission,
            **features,
        )


__all__ = [
    "DEFAULT_KIRO_COMMAND",
    "KiroACPAdapter",
    "KiroACPAdapterConfig",
]
