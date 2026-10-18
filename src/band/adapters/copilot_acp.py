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

import os
import shutil
from pathlib import Path
from typing import ClassVar

from acp.transports import default_environment
from pydantic import Field
from typing_extensions import Unpack

from band.core.exceptions import BandConfigError, CleanupIncompleteError
from band.core.model_catalog import ModelCatalog, ModelChoice
from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    PermissionResolver,
)
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")


class CopilotACPAdapterConfig(ACPClientAdapterConfig):
    """Settings for the Copilot CLI ACP backend.

    Inherits every :class:`ACPClientAdapterConfig` setting. ``model`` and
    ``reasoning_effort`` are only advertised by GitHub-hosted sessions; under
    BYOK the provider env (``COPILOT_MODEL``) picks the model.

    Attributes:
        command: The ``copilot`` launch command.
        github_token: Sets ``GITHUB_TOKEN`` for the CLI unless ``env``
            already does.
    """

    retention_unavailable_reason: ClassVar[str] = (
        "Copilot ACP scheduled prompts can outlive end_turn and are lost on "
        "process shutdown, even when the same session reloads successfully"
    )

    command: tuple[str, ...] = DEFAULT_COPILOT_COMMAND
    github_token: str | None = Field(default=None, repr=False)


class CopilotACPAdapter(ACPClientAdapter[CopilotACPAdapterConfig]):
    """Band adapter for the GitHub Copilot CLI over ACP.

    A thin specialization of :class:`ACPClientAdapter` that presets the Copilot
    command, no-op profile (default), and ``GITHUB_TOKEN`` environment.

    ``probe_models`` optionally uses the Copilot SDK to discover the configured
    account's hosted models without opening an ACP session or a Band room.
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
            workspace_for_room=workspace_for_room,
            resolve_session_config=resolve_session_config,
            resolve_permission=resolve_permission,
            **features,
        )

    def _credential_env(self) -> dict[str, str]:
        token = self.config.github_token
        return {"GITHUB_TOKEN": token} if token else {}

    async def probe_models(self, *, timeout_s: float = 60.0) -> ModelCatalog:
        """List hosted-account models without a room, session or model turn.

        Requires the optional ``copilot_sdk`` extra. Unlike session config
        discovery, this does not discover BYOK models or prove that configured
        inference, MCP servers or tools are ready. Failed cleanup remains owned
        by this adapter; retry it with ``cleanup_all``.
        """
        try:
            from copilot import CopilotClient, RuntimeConnection  # noqa: PLC0415 -- catalog-only SDK remains optional for ordinary ACP use
        except ImportError:
            raise BandConfigError(
                "Copilot model discovery requires the optional Copilot SDK. "
                "Install band-sdk[copilot_sdk] and retry."
            ) from None

        owner = self._harness_probe

        async def discover() -> ModelCatalog:
            client: CopilotClient | None = None

            async def close() -> None:
                nonlocal client
                if client is None:
                    return
                try:
                    await client.stop()
                except Exception as error:  # noqa: BLE001 -- retain any failed SDK close without exposing credentials
                    raise CleanupIncompleteError(
                        "Copilot model probe process cleanup failed "
                        f"({type(error).__name__})."
                    ) from None
                client = None

            owner.own(close)
            try:
                env = default_environment()
                env.update(self._spawn_env() or {})
                token = next(
                    (
                        env[name]
                        for name in (
                            "COPILOT_GITHUB_TOKEN",
                            "GH_TOKEN",
                            "GITHUB_TOKEN",
                        )
                        if env.get(name)
                    ),
                    None,
                )
                command = self.config.command
                cwd = (
                    Path(self.config.cwd).resolve()
                    if self.config.cwd is not None
                    else Path.cwd()
                )
                if os.path.dirname(command[0]):
                    executable = str((cwd / command[0]).resolve())
                else:
                    # The SDK otherwise searches the host PATH at the host cwd,
                    # not the environment and directory of its configured child.
                    search_path = os.pathsep.join(
                        str((cwd / entry).resolve()) for entry in os.get_exec_path(env)
                    )
                    executable = shutil.which(command[0], path=search_path)
                    if executable is None:
                        raise FileNotFoundError
                client = CopilotClient(
                    connection=RuntimeConnection.for_stdio(
                        path=executable,
                        args=[arg for arg in command[1:] if arg != "--acp"],
                    ),
                    env=env,
                    working_directory=self.config.cwd,
                    github_token=token,
                )
                await client.start()
                models = await client.list_models()
                return ModelCatalog(
                    models=tuple(
                        ModelChoice(
                            id=model.id,
                            label=model.name,
                            efforts=(
                                tuple(model.supported_reasoning_efforts)
                                if model.supported_reasoning_efforts is not None
                                else None
                            ),
                            default_effort=model.default_reasoning_effort,
                        )
                        for model in models
                    )
                )
            except CleanupIncompleteError:
                raise
            except FileNotFoundError:
                raise BandConfigError(
                    "The configured Copilot executable was not found. Install "
                    "Copilot or correct command, env['PATH'] and cwd, then retry."
                ) from None
            except Exception as error:  # noqa: BLE001 -- sanitize arbitrary SDK errors before owned cleanup starts
                raise BandConfigError(
                    f"Copilot model discovery failed ({type(error).__name__}). "
                    "Check the configured command, environment and Copilot "
                    "account credentials, then retry."
                ) from None

        try:
            return await owner.run(discover, timeout_s=timeout_s)
        except CleanupIncompleteError:
            raise
        except TimeoutError:
            raise BandConfigError(
                "Copilot model discovery timed out. Check the configured CLI "
                "and credentials, or increase timeout_s, then retry."
            ) from None


__all__ = [
    "DEFAULT_COPILOT_COMMAND",
    "CopilotACPAdapter",
    "CopilotACPAdapterConfig",
]
