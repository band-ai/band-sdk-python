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
Pass whatever your chosen method needs via ``env`` (``github_token`` is a
convenience that sets ``GITHUB_TOKEN``); leave both unset to use the CLI's
ambient login.

Band tools reach Copilot over MCP. When co-located with the SDK, keep
``inject_band_tools=True`` (a loopback HTTP/SSE MCP server). For a remote Copilot
that cannot reach the SDK host's loopback, set ``inject_band_tools=False`` and
pass an explicit reachable ``mcp_servers`` entry instead.
"""

from __future__ import annotations

from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    PermissionResolver,
)
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver, workspace_resolver_for

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")


class CopilotACPAdapterConfig(ACPClientAdapterConfig):
    """Settings for the Copilot CLI ACP backend.

    Inherits every :class:`ACPClientAdapterConfig` setting. ``model`` and
    ``reasoning_effort`` are only advertised by GitHub-hosted sessions; under
    BYOK the provider env (``COPILOT_MODEL``) picks the model.

    Attributes:
        command: The ``copilot`` launch command.
        cwd: Root under which each room gets its own workspace directory;
            exclusive with the adapter's ``workspace_for_room``.
        github_token: Sets ``GITHUB_TOKEN`` for the CLI unless ``env``
            already does.
    """

    command: tuple[str, ...] = DEFAULT_COPILOT_COMMAND
    cwd: str | None = None
    github_token: str | None = None


class CopilotACPAdapter(ACPClientAdapter[CopilotACPAdapterConfig]):
    """Band adapter for the GitHub Copilot CLI over ACP.

    A thin specialization of :class:`ACPClientAdapter` that presets the Copilot
    command, no-op profile (default), and ``GITHUB_TOKEN`` environment.
    """

    def __init__(
        self,
        config: CopilotACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge Band rooms to ``copilot --acp``.

        Args:
            config: The Copilot command, auth and bridge settings.
            additional_tools: Custom tools served next to the Band tools.
            workspace_for_room: Maps a room id to its absolute workspace;
                exclusive with ``config.cwd``.
            resolve_session_config: Picks session config options; exclusive
                with ``config.model`` and ``config.reasoning_effort``.
            resolve_permission: Chooses a permission option per tool call.
        """
        config = config or CopilotACPAdapterConfig()
        super().__init__(
            config,
            additional_tools=additional_tools,
            workspace_for_room=workspace_resolver_for(config.cwd, workspace_for_room),
            resolve_session_config=resolve_session_config,
            resolve_permission=resolve_permission,
            **features,
        )

    def _spawn_env(self) -> dict[str, str] | None:
        env = dict(self.config.env or {})
        if self.config.github_token:
            env.setdefault("GITHUB_TOKEN", self.config.github_token)
        return env or None


__all__ = [
    "DEFAULT_COPILOT_COMMAND",
    "CopilotACPAdapter",
    "CopilotACPAdapterConfig",
]
