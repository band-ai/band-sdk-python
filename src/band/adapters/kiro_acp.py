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

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    PermissionResolver,
)
from band.integrations.acp.client_profiles import KiroACPClientProfile
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver

DEFAULT_KIRO_COMMAND: tuple[str, ...] = ("kiro-cli", "acp")


class KiroACPAdapterConfig(ACPClientAdapterConfig):
    """Settings for the Kiro CLI ACP backend.

    Inherits every :class:`ACPClientAdapterConfig` setting.

    Attributes:
        command: The ``kiro-cli acp`` launch command.
        env: Extra subprocess environment, e.g. ``KIRO_API_KEY`` for headless
            auth or ``KIRO_HOME`` to isolate state.
    """

    command: tuple[str, ...] = DEFAULT_KIRO_COMMAND


class KiroACPAdapter(ACPClientAdapter[KiroACPAdapterConfig]):
    """Thin ``ACPClientAdapter`` specialization for ``kiro-cli acp`` (stdio)."""

    def __init__(
        self,
        config: KiroACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge Band rooms to ``kiro-cli acp``.

        Args:
            config: The Kiro command and bridge settings.
            additional_tools: Custom tools served next to the Band tools.
            workspace_for_room: Maps a room id to its absolute workspace;
                exclusive with ``config.cwd``.
            resolve_session_config: Picks session config options.
            resolve_permission: Chooses a permission option per tool call.
        """
        super().__init__(
            config or KiroACPAdapterConfig(),
            additional_tools=additional_tools,
            workspace_for_room=workspace_for_room,
            profile=KiroACPClientProfile(),
            resolve_session_config=resolve_session_config,
            resolve_permission=resolve_permission,
            **features,
        )


__all__ = [
    "DEFAULT_KIRO_COMMAND",
    "KiroACPAdapter",
    "KiroACPAdapterConfig",
]
