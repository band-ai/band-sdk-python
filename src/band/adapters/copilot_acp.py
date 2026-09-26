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
import os
from dataclasses import dataclass
from typing import Any, cast

from typing_extensions import Unpack

from band.core.harness import HarnessModel
from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    ACPClientAdapter,
    PermissionResolver,
    resolve_turn_timeout,
)
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver, workspace_resolver_for

logger = logging.getLogger(__name__)

DEFAULT_COPILOT_COMMAND: tuple[str, ...] = ("copilot", "--acp")
_MODEL_FLAG = "--model"
_REASONING_EFFORT_FLAG = "--reasoning-effort"


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
    resolve_permission: PermissionResolver | None = None
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_SECONDS
    # Appended to the stdio command as --model/--reasoning-effort. Copilot's
    # ACP session exposes no model option, and the CLI accepts an unknown
    # model at session/new, so check values against list_models().
    model: str | None = None
    reasoning_effort: str | None = None


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
        if use_tcp and (config.model or config.reasoning_effort):
            raise ValueError(
                "model/reasoning_effort are CLI flags and need the stdio transport; "
                "configure them on the TCP server instead"
            )

        # A rejected remote transport owns its environment, so any auth supplied
        # with it is ignored after this warning.
        if use_tcp and (config.github_token or config.env):
            logger.warning(
                "github_token/env are ignored over TCP: the already-running "
                "copilot --acp server owns its own environment; configure auth on "
                "that server instead."
            )

        env = None if use_tcp else _spawn_env(config)

        workspace_for_room = workspace_resolver_for(
            config.cwd, config.workspace_for_room
        )

        common: dict[str, Any] = {
            "env": env,
            "workspace_for_room": workspace_for_room,
            "mcp_servers": config.mcp_servers,
            "additional_tools": additional_tools,
            "inject_band_tools": config.inject_band_tools,
            "custom_section": config.custom_section,
            "resolve_session_config": config.resolve_session_config,
            "resolve_permission": config.resolve_permission,
            "turn_timeout_s": resolve_turn_timeout(
                config.turn_timeout_s, cast("dict[str, Any]", features)
            ),
        }

        if use_tcp:
            super().__init__(host=config.host, port=config.port, **common, **features)
        else:
            super().__init__(
                command=_command_with_model_flags(config), **common, **features
            )


def _spawn_env(config: CopilotACPAdapterConfig) -> dict[str, str] | None:
    """Auth/env for a spawned CLI: ``env`` over ``github_token``'s GITHUB_TOKEN.

    ``None`` leaves the CLI on its ambient login.
    """
    env = dict(config.env or {})
    if config.github_token:
        env.setdefault("GITHUB_TOKEN", config.github_token)
    return env or None


def _command_with_model_flags(config: CopilotACPAdapterConfig) -> list[str]:
    """``command`` plus the typed model flags, each given exactly once."""
    command = list(config.command)
    for flag, value in (
        (_MODEL_FLAG, config.model),
        (_REASONING_EFFORT_FLAG, config.reasoning_effort),
    ):
        if value is None:
            continue
        if any(arg == flag or arg.startswith(f"{flag}=") for arg in command):
            raise ValueError(
                f"{flag} is set both in command and as a typed config field"
            )
        command.extend((flag, value))
    return command


def _probe_env(config: CopilotACPAdapterConfig) -> dict[str, str] | None:
    """The listing client's full child environment.

    ``CopilotClient`` hands ``env`` to the subprocess as-is, so the host's
    environment (PATH, HOME, proxy and cert settings) is copied under the
    configured overrides rather than replaced by them. ``None`` inherits.
    """
    overrides = _spawn_env(config)
    return None if overrides is None else {**os.environ, **overrides}


async def list_models(
    config: CopilotACPAdapterConfig | None = None,
) -> list[HarnessModel]:
    """The models the installed Copilot CLI offers this account.

    Uses ``github-copilot-sdk`` (the ``copilot_sdk`` extra) against the
    executable in ``config.command`` with the configured auth; no session
    or model turn. The client is stopped on every exit path, including a
    start that fails partway. Tested with Copilot CLI 1.0.88 and
    github-copilot-sdk 1.0.14.
    """
    # Deferred: github-copilot-sdk is the optional copilot_sdk extra, absent
    # from lanes that import this module for the ACP adapter alone.
    from copilot import (  # noqa: PLC0415 -- copilot_sdk extra, see above
        CopilotClient,
        StdioRuntimeConnection,
    )

    config = config or CopilotACPAdapterConfig()
    client = CopilotClient(
        connection=StdioRuntimeConnection(path=config.command[0]),
        env=_probe_env(config),
    )
    try:
        await client.start()
        listed = await client.list_models()
    finally:
        await client.stop()
    return [
        HarnessModel(
            id=model.id,
            label=model.name,
            efforts=tuple(model.supported_reasoning_efforts or ()),
            default_effort=model.default_reasoning_effort,
        )
        for model in listed
    ]


__all__ = [
    "DEFAULT_COPILOT_COMMAND",
    "CopilotACPAdapter",
    "CopilotACPAdapterConfig",
    "list_models",
]
