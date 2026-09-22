"""OMP (oh-my-pi) adapter over ACP."""

from __future__ import annotations

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
from pydantic import JsonValue
from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    PermissionResolver,
    SpawnProcess,
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
    is_omp_approve_deny_form,
    normalize_omp_mcp_device_call,
    omp_elicitation_call_id,
)
from band.runtime.custom_tools import CustomToolDef

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
    """Runtime configuration for OMP over ACP (stdio only)."""

    command: tuple[str, ...] = DEFAULT_OMP_ACP_COMMAND
    cwd: str | None = None
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    resolve_permission: PermissionResolver | None = None


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
        super().__init__(
            command=finalize_omp_command(config.command),
            env=config.env,
            cwd=config.cwd,
            mcp_servers=config.mcp_servers,
            additional_tools=additional_tools,
            inject_band_tools=config.inject_band_tools,
            custom_section=config.custom_section,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=config.resolve_permission,
            client_capabilities=_OMP_FORM_CAPABILITIES,
            use_unstable_protocol=True,
            spawn_process=spawn_process,
            **features,
        )

    def _runtime_client_factory(self) -> OmpACPCollectingClient:
        return OmpACPCollectingClient(
            own_tool_names=self._own_tool_names,
            canonicalize_tool_name=self._canonical_tool_name,
        )

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
            if not is_omp_approve_deny_form(requested_schema):
                logger.debug(
                    "Declining unsupported OMP elicitation form for session %s",
                    session_id,
                )
                return DeclineElicitationResponse(action="decline")

            field = approve_deny_form_field(requested_schema)
            assert field is not None
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
            narration = emitter.open_permission(
                call=synthetic_call,
                session_id=session_id,
                outcome="cancelled",
            )
            if narrate_elicitation is None:
                await narration
            else:
                await narrate_elicitation(narration)
            return DeclineElicitationResponse(action="decline")

        return handler


__all__ = [
    "DEFAULT_OMP_ACP_COMMAND",
    "OmpACPAdapter",
    "OmpACPAdapterConfig",
]
