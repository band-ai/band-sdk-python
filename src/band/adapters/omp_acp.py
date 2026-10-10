"""OMP (oh-my-pi) adapter over ACP."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal, Self, cast

from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    DeclineElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    PermissionOption,
)
from pydantic import Field, JsonValue, field_validator, model_validator
from typing_extensions import Unpack

from band.core.model_catalog import ModelSelection
from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    PermissionResolver,
    SpawnProcess,
)
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.permissions import (
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
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVE_OPTION_ID,
    OMP_DENY_OPTION_ID,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    approve_deny_form_field,
    finalize_omp_command,
    normalize_omp_mcp_device_call,
    omp_command_in_workspace,
    omp_elicitation_call_id,
    omp_provider_env,
    validate_omp_command,
)
from band.runtime.custom_tools import CustomToolDef
from band.workspaces import WorkspaceResolver

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
            kind=getattr(update, "kind", None),
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


class OmpACPAdapterConfig(ACPClientAdapterConfig):
    """Settings for OMP over ACP (stdio only).

    Inherits every :class:`ACPClientAdapterConfig` setting except ``model``.

    Attributes:
        approval_mode: OMP's native approval mode, appended to ``command``.
            ``"yolo"`` gives the agent full access to its host.
        command: The ``omp acp`` launch command; approval flags other than
            ``approval_mode`` are rejected.
        model: OMP's provider-qualified model (e.g. ``openai/gpt-6-luna``),
            passed as OMP's ``--model`` launch flag rather than selected from
            the session's catalog.
        api_key: Passed in the env var of ``model``'s provider; needs ``model``.
            An explicit ``env`` entry for that var wins.
        use_unstable_protocol: Always on: OMP asks for tool approval through
            unstable elicitation forms.
    """

    approval_mode: Literal["always-ask", "yolo"] = OMP_APPROVAL_MODE_ALWAYS_ASK
    command: tuple[str, ...] = DEFAULT_OMP_ACP_COMMAND
    model: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    use_unstable_protocol: Literal[True] = True

    @field_validator("command")
    @classmethod
    def _reject_unsafe_flags(cls, command: tuple[str, ...]) -> tuple[str, ...]:
        validate_omp_command(command)
        return command

    @model_validator(mode="after")
    def _api_key_needs_model(self) -> Self:
        if self.api_key is not None and self.model is None:
            raise ValueError(
                "api_key needs model: the provider-qualified model picks which "
                "provider key env var carries the key"
            )
        return self


class OmpACPAdapter(ACPClientAdapter[OmpACPAdapterConfig]):
    """Thin ``ACPClientAdapter`` specialization for ``omp acp`` (stdio)."""

    def __init__(
        self,
        config: OmpACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        spawn_process: SpawnProcess | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge Band rooms to ``omp acp``.

        Args:
            config: The OMP command, approval mode and bridge settings.
            additional_tools: Custom tools served next to the Band tools.
            workspace_for_room: Maps a room id to its absolute workspace;
                exclusive with ``config.cwd``.
            resolve_session_config: Picks session config options.
            resolve_permission: Chooses a permission option per tool call and
                per OMP approval form.
            spawn_process: Rejected: a custom transport cannot guarantee one
                process per room.
        """
        config = config or OmpACPAdapterConfig()
        super().__init__(
            config,
            additional_tools=additional_tools,
            workspace_for_room=workspace_for_room,
            resolve_session_config=resolve_session_config,
            resolve_permission=resolve_permission,
            client_capabilities=_OMP_FORM_CAPABILITIES,
            spawn_process=spawn_process,
            **features,
        )
        self._omp_command = finalize_omp_command(
            config.command, model=config.model, approval_mode=config.approval_mode
        )

    @property
    def model_selection(self) -> ModelSelection:
        """OMP gets ``config.model`` as a launch flag, so only the effort is
        selected per session."""
        return super().model_selection.model_copy(update={"model": None})

    def _credential_env(self) -> dict[str, str]:
        if self.config.api_key is None or self.config.model is None:
            return {}
        return omp_provider_env(model=self.config.model, api_key=self.config.api_key)

    def _runtime_client_factory(self) -> OmpACPCollectingClient:
        return OmpACPCollectingClient(
            own_tool_names=self._own_tool_names,
            canonicalize_tool_name=self._canonical_tool_name,
        )

    def _spawn_command(self, workspace: str | None) -> list[str]:
        if workspace is None:
            return list(self._omp_command)
        # omp's own --cwd flag ("Directory to start in (overrides the launch
        # cwd)") gives the same per-room isolation _spawn_cwd would otherwise
        # provide via the subprocess-level cwd -- confirmed live: a bash
        # tool's `pwd`/`ls` inside the session reports this directory, not
        # the subprocess's actual launch dir. See _spawn_cwd for why that
        # path is avoided instead.
        return omp_command_in_workspace(self._omp_command, workspace)

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
            form_field = approve_deny_form_field(requested_schema)
            if form_field is None:
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
                    content={form_field: OMP_FORM_APPROVE},
                )
            await self._narrate_cancelled_permission(
                call=synthetic_call,
                session_id=session_id,
                emitter=emitter,
                narrate=narrate_elicitation,
            )
            return DeclineElicitationResponse(action="decline")

        return handler


__all__ = [
    "DEFAULT_OMP_ACP_COMMAND",
    "OmpACPAdapter",
    "OmpACPAdapterConfig",
]
