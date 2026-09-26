"""OMP (oh-my-pi) adapter over ACP."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    DeclineElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    PermissionOption,
    SessionConfigOptionSelect,
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
    ACPRuntime,
    ElicitationHandler,
    ElicitationNarrator,
    elicitation_requested_schema,
)
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.session_config import (
    SessionConfigOption,
    SessionConfigResolver,
    flatten_select_options,
    session_config_options,
)
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

_OMP_MODEL_OPTION_ID = "model"
_OMP_THINKING_OPTION_ID = "thinking"

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

    async def release_room_resources(self, room_id: str) -> None:
        """Stop an idle room's ``omp acp`` process; the next turn reloads its session.

        OMP persists sessions and reloads them with ``session/load`` in a new
        process (verified live with OMP 18.3.2: a fact from the first turn was
        recalled after release).
        """
        await self._release_loadable_session(room_id)

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
    """The models this OMP install offers.

    Reads ``omp models --json``. When that command is unavailable (fails or
    prints something that is not its JSON listing), falls back to the
    ``model``/``thinking`` selects a throwaway ACP session advertises in
    ``configOptions``. No model turn runs, no room workspace or session is
    used, and every subprocess is closed on every exit path, cancellation
    included. Tested with OMP 18.3.2.
    """
    config = config or OmpACPAdapterConfig()
    try:
        return await _models_from_cli(config)
    except (RuntimeError, ValueError) as exc:
        logger.info(
            "omp models --json unavailable (%s); reading ACP session configOptions",
            exc,
        )
        return await _models_from_acp_session(config)


async def _models_from_cli(config: OmpACPAdapterConfig) -> list[HarnessModel]:
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
    listing = json.loads(stdout)
    if not isinstance(listing, dict) or not isinstance(listing.get("models"), list):
        raise ValueError("`omp models --json` printed no model listing")
    return omp_models(listing)


async def _models_from_acp_session(config: OmpACPAdapterConfig) -> list[HarnessModel]:
    runtime = ACPRuntime(
        command=_resolve_launcher(finalize_omp_command(config.command)),
        env=config.env,
        client_capabilities=_OMP_FORM_CAPABILITIES,
        use_unstable_protocol=True,
    )
    try:
        await runtime.start()
        with tempfile.TemporaryDirectory(prefix="band-omp-probe-") as cwd:
            session = await runtime.create_session_response(cwd=cwd, mcp_servers=[])
            try:
                return omp_session_models(session_config_options(session) or ())
            finally:
                await runtime.close_session(session.session_id)
    finally:
        await runtime.stop()


def omp_session_models(
    options: Sequence[SessionConfigOption],
) -> list[HarnessModel]:
    """Models from an OMP session's ``model`` select.

    The ``thinking`` select is session-wide, so its levels are attributed
    only to the session's current model, the one they apply to.
    """
    selects = {
        option.id: option
        for option in options
        if isinstance(option, SessionConfigOptionSelect)
    }
    model_select = selects.get(_OMP_MODEL_OPTION_ID)
    if model_select is None:
        raise RuntimeError("OMP's ACP session advertises no model option")
    thinking = selects.get(_OMP_THINKING_OPTION_ID)
    levels = (
        tuple(o.value for o in flatten_select_options(thinking.options))
        if thinking is not None
        else ()
    )
    current = model_select.current_value
    return [
        HarnessModel(
            id=option.value,
            label=option.name or option.value,
            provider=option.value.partition("/")[0] or None,
            efforts=levels if option.value == current else (),
            default_effort=(
                thinking.current_value
                if thinking is not None and option.value == current
                else None
            ),
            is_default=option.value == current,
        )
        for option in flatten_select_options(model_select.options)
    ]


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
    "omp_session_models",
]
