"""OMP (oh-my-pi) adapter over ACP."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    DeclineElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    PermissionOption,
)
from acp.transports import default_environment
from pydantic import JsonValue
from typing_extensions import Unpack

from band.core.harness import HarnessModel
from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    ACPClientAdapter,
    PermissionResolver,
    SpawnProcess,
    _resolve_launcher,
    resolve_turn_timeout,
)
from band.integrations.acp.client_runtime import (
    ACPCollectingClient,
    ElicitationHandler,
    ElicitationNarrator,
    elicitation_requested_schema,
)
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.session_config import SessionConfigResolver
from band.integrations.acp.types import ACPToolCall
from band.integrations.omp import (
    DEFAULT_OMP_ACP_COMMAND,
    OMP_APPROVAL_FORM_TOOL_NAME,
    OMP_APPROVE_OPTION_ID,
    OMP_DENY_OPTION_ID,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    approve_deny_form_field,
    finalize_omp_command,
    normalize_omp_mcp_device_call,
    omp_elicitation_call_id,
)
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver, create_room_workspace_resolver

logger = logging.getLogger(__name__)

_OMP_FORM_CAPABILITIES = ClientCapabilities(
    elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities())
)


class OmpACPCollectingClient(ACPCollectingClient):
    """ACP client that rewrites OMP MCP device writes before narration."""

    def __init__(
        self,
        *,
        own_tool_names: frozenset[str],
        canonicalize_tool_name: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(canonicalize_tool_name=canonicalize_tool_name)
        self._own_tool_names = own_tool_names

    def _tool_call_chunk(self, update: object):
        chunk = super()._tool_call_chunk(update)
        if chunk.tool is None or not isinstance(chunk.tool, ACPToolCall):
            return chunk
        name, args = normalize_omp_mcp_device_call(
            chunk.tool.name,
            chunk.tool.arguments,
            self._own_tool_names,
        )
        if name == chunk.tool.name and args == chunk.tool.arguments:
            return chunk
        chunk.tool = ACPToolCall(
            tool_call_id=chunk.tool.tool_call_id,
            name=name,
            arguments=cast(dict[str, JsonValue], args),
        )
        chunk.content = name
        return chunk


@dataclass(frozen=True)
class OmpACPAdapterConfig:
    """Runtime configuration for OMP over ACP (stdio only).

    ``cwd`` is a compatibility alias for a workspace root; prefer
    ``workspace_for_room`` for new code.
    """

    command: tuple[str, ...] = DEFAULT_OMP_ACP_COMMAND
    cwd: str | None = None
    workspace_for_room: WorkspaceResolver | None = None
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    resolve_permission: PermissionResolver | None = None
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_SECONDS


class OmpACPAdapter(ACPClientAdapter):
    """Thin ``ACPClientAdapter`` specialization for ``omp acp`` (stdio)."""

    def __init__(
        self,
        config: OmpACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        spawn_process: SpawnProcess | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or OmpACPAdapterConfig()
        workspace_for_room = config.workspace_for_room
        if config.cwd is not None:
            if workspace_for_room is not None:
                raise ValueError("set either cwd or workspace_for_room, not both")
            workspace_for_room = create_room_workspace_resolver(config.cwd)
        turn_timeout_s = resolve_turn_timeout(
            config.turn_timeout_s, cast("dict[str, Any]", features)
        )
        super().__init__(
            command=finalize_omp_command(config.command),
            env=config.env,
            workspace_for_room=workspace_for_room,
            mcp_servers=config.mcp_servers,
            additional_tools=additional_tools,
            inject_band_tools=config.inject_band_tools,
            custom_section=config.custom_section,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=config.resolve_permission,
            client_capabilities=_OMP_FORM_CAPABILITIES,
            use_unstable_protocol=True,
            spawn_process=spawn_process,
            turn_timeout_s=turn_timeout_s,
            **features,
        )

    def _runtime_client_factory(self) -> OmpACPCollectingClient:
        return OmpACPCollectingClient(
            own_tool_names=self._own_tool_names,
            canonicalize_tool_name=self._canonical_tool_name,
        )

    def _spawn_command(self, workspace: str | None) -> list[str]:
        if workspace is None:
            return self._command
        # omp's own --cwd flag ("Directory to start in (overrides the launch
        # cwd)") gives the same per-room isolation _spawn_cwd would otherwise
        # provide via the subprocess-level cwd -- confirmed live: a bash
        # tool's `pwd`/`ls` inside the session reports this directory, not
        # the subprocess's actual launch dir. See _spawn_cwd for why that
        # path is avoided instead.
        acp_index = self._command.index("acp")
        return [
            *self._command[: acp_index + 1],
            f"--cwd={workspace}",
            *self._command[acp_index + 1 :],
        ]

    def _spawn_cwd(self, workspace: str | None) -> str | None:
        del workspace
        # omp's Bun runtime completes the ACP handshake and session setup
        # fine, then goes silent -- or degrades into a slow permission-request
        # retry loop that never finishes -- on its first real turn when the
        # *subprocess itself* is spawned with an explicit cwd. CPython's
        # subprocess machinery only takes the fast posix_spawn() path when
        # cwd is None, falling back to fork()+chdir() otherwise, and that
        # fork()-based path is what breaks omp (never observed with
        # codex-acp/copilot/cursor). omp's own --cwd flag (see
        # _spawn_command) sidesteps this entirely.
        return None

    def _make_elicitation_handler(
        self,
        emitter: RoomTurnEmitter,
        room_id: str,
        session_id: str,
    ) -> ElicitationHandler | None:
        async def handler(
            *,
            message: str,
            mode: object,
            narrate_elicitation: ElicitationNarrator | None = None,
            **kwargs: object,
        ) -> object:
            requested_schema = elicitation_requested_schema(mode, kwargs)
            field = approve_deny_form_field(requested_schema)
            if field is None:
                logger.debug(
                    "Declining unsupported OMP elicitation form for session %s",
                    session_id,
                )
                return DeclineElicitationResponse(action="decline")

            synthetic_call = ACPToolCall(
                tool_call_id=omp_elicitation_call_id(session_id),
                name=OMP_APPROVAL_FORM_TOOL_NAME,
                arguments={"message": message},
            )
            options = (
                PermissionOption(
                    optionId=OMP_APPROVE_OPTION_ID,
                    name=OMP_FORM_APPROVE,
                    kind="allow_once",
                ),
                PermissionOption(
                    optionId=OMP_DENY_OPTION_ID,
                    name=OMP_FORM_DENY,
                    kind="reject_once",
                ),
            )
            # Mirrors _make_permission_handler: always defer to
            # _resolve_permission_option, which auto-approves via
            # select_allow_option_id when no resolver is configured. Gating
            # this call on self._resolve_permission being set (as before)
            # left every OMP MCP/tool-call approval -- which OMP routes
            # through this elicitation form, not session/request_permission
            # -- declined by default, since most callers never configure a
            # custom resolver.
            option_id = await self._resolve_permission_option(
                call=synthetic_call,
                options=options,
                room_id=room_id,
                session_id=session_id,
            )
            if option_id == OMP_APPROVE_OPTION_ID:
                return AcceptElicitationResponse(
                    action="accept",
                    content={field: OMP_FORM_APPROVE},
                )
            await self._narrate_cancelled_permission(
                call=synthetic_call,
                session_id=session_id,
                emitter=emitter,
                narrate=narrate_elicitation,
            )
            return DeclineElicitationResponse(action="decline")

        return handler


async def list_models(config: OmpACPAdapterConfig | None = None) -> list[HarnessModel]:
    """The models this OMP install offers, from ``omp models --json``.

    Runs the configured ``omp`` executable with the configured environment;
    no ACP session or model turn. The subprocess is killed if the call is
    cancelled. Tested with OMP 18.3.2.
    """
    config = config or OmpACPAdapterConfig()
    [omp] = _resolve_launcher([config.command[0]])
    proc = await asyncio.create_subprocess_exec(
        omp,
        "models",
        "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**default_environment(), **(config.env or {})},
    )
    try:
        stdout, stderr = await proc.communicate()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{omp} models --json` exited {proc.returncode}: "
            f"{stderr.decode(errors='replace').strip()}"
        )
    return omp_models(json.loads(stdout))


def omp_models(listing: dict[str, Any]) -> list[HarnessModel]:
    """Parse ``omp models --json``; ``id`` is the ``provider/model`` selector."""
    return [
        HarnessModel(
            id=str(entry.get("selector") or entry["id"]),
            label=str(entry.get("name") or entry["id"]),
            provider=entry.get("provider"),
            efforts=tuple(entry.get("thinking") or ()),
        )
        for entry in listing.get("models") or []
        if isinstance(entry, dict) and entry.get("id")
    ]


__all__ = [
    "DEFAULT_OMP_ACP_COMMAND",
    "OmpACPAdapter",
    "OmpACPAdapterConfig",
    "list_models",
    "omp_models",
]
