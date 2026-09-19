"""Oh My P.I. adapter over ACP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from acp.schema import (
    ClientCapabilities,
    CreateElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationMode,
    PermissionOption,
)
from pydantic import JsonValue
from typing_extensions import Unpack

from band.core.types import FeatureKwargs
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    PermissionResolver,
)
from band.integrations.acp.client_runtime import (
    ACP_PERMISSION_ALLOW_ONCE,
    ACP_PERMISSION_REJECT_ONCE,
    ElicitationHandler,
    accept_elicitation,
    decline_elicitation,
)
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.session_config import SessionConfigResolver
from band.integrations.acp.types import ACPToolCall, PermissionOutcome
from band.integrations.omp import (
    OMP_APPROVAL_MODE_ARGUMENT,
    OMP_AUTO_APPROVE_ARGUMENT,
    OMP_COMMAND,
    OMP_ELICITATION_APPROVE_OPTION,
    OMP_ELICITATION_CALL_ID_PREFIX,
    OMP_ELICITATION_DENY_OPTION,
    OMP_ELICITATION_FIELD,
    OMP_ELICITATION_MESSAGE_FIELD,
    OMP_ELICITATION_TOOL_NAME,
    OMP_MCP_CONTENT_FIELD,
    OMP_MCP_DEVICE_PREFIX,
    OMP_MCP_PATH_FIELD,
    OMP_YOLO_APPROVAL_MODE,
    OMP_YOLO_ARGUMENT,
)
from band.runtime.custom_tools import CustomToolDef

DEFAULT_OMP_ACP_COMMAND = OMP_COMMAND
_UNSAFE_APPROVAL_ARGUMENTS = frozenset({OMP_YOLO_ARGUMENT, OMP_AUTO_APPROVE_ARGUMENT})


@dataclass(frozen=True)
class OmpACPAdapterConfig:
    """Runtime configuration for an ``omp acp`` stdio backend."""

    command: tuple[str, ...] = DEFAULT_OMP_ACP_COMMAND
    cwd: str | None = None
    env: dict[str, str] | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, Any]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    resolve_permission: PermissionResolver | None = None


class OmpACPAdapter(ACPClientAdapter):
    """Band adapter for Oh My P.I.'s native ACP stdio server."""

    def __init__(
        self,
        config: OmpACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or OmpACPAdapterConfig()
        if _uses_unsafe_approval(config.command):
            raise ValueError(
                "OMP auto-approve modes bypass Band permission resolution; "
                "use an approval-gated mode instead"
            )
        super().__init__(
            command=list(config.command),
            cwd=config.cwd,
            env=config.env,
            custom_section=config.custom_section,
            inject_band_tools=config.inject_band_tools,
            mcp_servers=config.mcp_servers,
            additional_tools=additional_tools,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=config.resolve_permission,
            client_capabilities=ClientCapabilities(
                elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities())
            ),
            use_unstable_protocol=True,
            **features,
        )

    def _make_elicitation_handler(
        self,
        emitter: RoomTurnEmitter,
        room_id: str,
    ) -> ElicitationHandler:
        async def handler(
            message: str,
            mode: ElicitationMode,
            **kwargs: Any,
        ) -> CreateElicitationResponse:
            del kwargs
            session_id, choices = _omp_elicitation_details(mode)
            if session_id is None or choices is None:
                return decline_elicitation()

            call = ACPToolCall(
                tool_call_id=f"{OMP_ELICITATION_CALL_ID_PREFIX}:{session_id}",
                name=OMP_ELICITATION_TOOL_NAME,
                arguments={OMP_ELICITATION_MESSAGE_FIELD: message},
            )
            option_id = await self._resolve_permission_option(
                call=call,
                options=_omp_elicitation_options(choices),
                room_id=room_id,
                session_id=session_id,
            )
            if option_id == OMP_ELICITATION_APPROVE_OPTION:
                return accept_elicitation({OMP_ELICITATION_FIELD: option_id})

            await emitter.open_permission(
                call=call,
                session_id=session_id,
                outcome=PermissionOutcome.CANCELLED,
            )
            return decline_elicitation()

        return handler

    def _normalize_acp_tool_call(
        self,
        tool_call: object,
    ) -> tuple[str, dict[str, JsonValue]] | None:
        """Decode OMP's ``xd://mcp__`` transport call into a Band MCP invocation."""
        raw_input = getattr(tool_call, "raw_input", None)
        if not isinstance(raw_input, dict):
            return None
        path = raw_input.get(OMP_MCP_PATH_FIELD)
        if not isinstance(path, str):
            return None
        tool_name = _omp_band_tool_name(path, self._own_tool_names)
        if tool_name is None:
            return None
        content = raw_input.get(OMP_MCP_CONTENT_FIELD)
        if not isinstance(content, str):
            return None
        try:
            arguments = json.loads(content)
        except json.JSONDecodeError:
            return None
        return (tool_name, arguments) if isinstance(arguments, dict) else None


def _uses_unsafe_approval(command: tuple[str, ...]) -> bool:
    """Whether an OMP command disables permission-gated tool execution."""
    return bool(_UNSAFE_APPROVAL_ARGUMENTS.intersection(command)) or any(
        option == OMP_APPROVAL_MODE_ARGUMENT and value == OMP_YOLO_APPROVAL_MODE
        for option, value in zip(command, command[1:])
    )


def _omp_elicitation_details(mode: object) -> tuple[str | None, tuple[str, ...] | None]:
    """Return an OMP approval form's session and exact response choices."""
    form = getattr(mode, "root", mode)
    session_id = getattr(form, "session_id", None)
    schema = getattr(form, "requested_schema", None)
    properties = getattr(schema, "properties", None)
    if not isinstance(properties, dict):
        return None, None
    value = properties.get(OMP_ELICITATION_FIELD)
    choices = getattr(value, "enum", None)
    if not isinstance(session_id, str) or not isinstance(choices, list):
        return None, None
    response_choices = tuple(choice for choice in choices if isinstance(choice, str))
    expected_choices = {
        OMP_ELICITATION_APPROVE_OPTION,
        OMP_ELICITATION_DENY_OPTION,
    }
    if (
        len(response_choices) != len(expected_choices)
        or set(response_choices) != expected_choices
    ):
        return None, None
    return session_id, response_choices


def _omp_elicitation_options(choices: tuple[str, ...]) -> tuple[PermissionOption, ...]:
    """Present OMP's form choices through the shared permission resolver."""
    return tuple(
        PermissionOption(
            optionId=choice,
            name=choice,
            kind=(
                ACP_PERMISSION_ALLOW_ONCE
                if choice == OMP_ELICITATION_APPROVE_OPTION
                else ACP_PERMISSION_REJECT_ONCE
            ),
        )
        for choice in choices
    )


def _omp_band_tool_name(path: str, own_names: frozenset[str]) -> str | None:
    """Return the registered Band tool represented by an OMP MCP device URL."""
    encoded_name = path.removeprefix(OMP_MCP_DEVICE_PREFIX)
    return encoded_name if encoded_name != path and encoded_name in own_names else None


__all__ = ["DEFAULT_OMP_ACP_COMMAND", "OmpACPAdapter", "OmpACPAdapterConfig"]
