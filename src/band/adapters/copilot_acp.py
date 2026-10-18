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

import asyncio
import os
import re
import subprocess
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

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

if TYPE_CHECKING:
    from copilot import CopilotClient

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")


def _require_supported_copilot_sdk() -> None:
    """Bound the teardown compatibility seam to released Copilot SDK 1.x."""
    requirement = (
        "Copilot model discovery requires a released github-copilot-sdk>=1.0.5,<2. "
        "Install band-sdk[copilot_sdk] with a supported SDK and retry."
    )
    try:
        version = metadata.version("github-copilot-sdk")
    except metadata.PackageNotFoundError:
        raise BandConfigError(requirement) from None
    release = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.post[0-9]+)?", version)
    if release is None or not (
        (1, 0, 5) <= tuple(map(int, release.groups())) < (2, 0, 0)
    ):
        raise BandConfigError(requirement)


def _copilot_processes(
    client: CopilotClient,
) -> tuple[subprocess.Popen[bytes] | subprocess.Popen[str], ...]:
    """Observe the known SDK 1.x fields without accepting an unknown handle type."""
    try:
        # SDK 1.x has no public exit handle and can erase these on failed stop.
        handles = (client._process, client._cli_process)
    except AttributeError:
        raise BandConfigError(
            "The Copilot SDK has an unsupported subprocess layout."
        ) from None
    if any(
        handle is not None and not isinstance(handle, subprocess.Popen)
        for handle in handles
    ):
        raise BandConfigError(
            "The Copilot SDK has unsupported subprocess handle types."
        )
    return tuple(handle for handle in handles if isinstance(handle, subprocess.Popen))


def _copilot_executable(command: str, env: dict[str, str], cwd: Path) -> str:
    """Resolve only configured absolute candidates, including Windows extensions."""
    qualified = bool(os.path.dirname(command))
    if qualified:
        command = str((cwd / command).resolve())
        if os.name != "nt":
            return command
    names = (command,)
    if os.name == "nt":
        extensions = tuple(
            extension
            for extension in env.get(
                "PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"
            ).split(os.pathsep)
            if extension
        )
        if not any(
            command.lower().endswith(extension.lower()) for extension in extensions
        ):
            names = tuple(command + extension for extension in extensions)
    # which(path=...) still searches the host cwd and PATHEXT on Windows.
    search_path = ("",) if qualified else os.get_exec_path(env)
    for entry in search_path:
        directory = (cwd / entry).resolve()
        for name in names:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise FileNotFoundError


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
            _require_supported_copilot_sdk()
            client: CopilotClient | None = None
            processes: list[subprocess.Popen[bytes] | subprocess.Popen[str]] = []

            async def close() -> None:
                nonlocal client
                if client is None:
                    return
                try:
                    for process in _copilot_processes(client):
                        if process not in processes:
                            processes.append(process)
                    await client.stop()
                    for process in processes:
                        if process.poll() is None:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=timeout_s)
                        if process.poll() is None:
                            raise CleanupIncompleteError(
                                "Copilot model probe child exit is unconfirmed."
                            )
                except Exception as error:  # noqa: BLE001 -- retain any failed SDK close without exposing credentials
                    raise CleanupIncompleteError(
                        "Copilot model probe process cleanup failed "
                        f"({type(error).__name__})."
                    ) from None
                client = None
                processes.clear()

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
                executable = _copilot_executable(command[0], env, cwd)
                args = [arg for arg in command[1:] if arg != "--acp"]
                if os.name == "nt" and executable.lower().endswith(".js"):
                    args.insert(0, executable)
                    # Preserve native node.exe selection without host image lookup.
                    executable = _copilot_executable(
                        "node", env | {"PATHEXT": ".exe"}, cwd
                    )
                candidate = CopilotClient(
                    connection=RuntimeConnection.for_stdio(
                        path=executable,
                        args=args,
                    ),
                    env=env,
                    working_directory=self.config.cwd,
                    github_token=token,
                )
                # Construction is inert; reject unknown layouts before startup.
                processes.extend(_copilot_processes(candidate))
                client = candidate
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
                    "Check the supported Copilot SDK version, configured command, "
                    "environment and Copilot account credentials, then retry."
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
