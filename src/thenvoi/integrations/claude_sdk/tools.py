"""
Shared Claude SDK MCP tool wrappers for Thenvoi tools.

This module keeps the Claude-specific SDK wrapping in one place so the adapter
and the legacy integration do not each maintain their own copy of the same
tool surface.
"""

from __future__ import annotations

import inspect
import json
import logging
import warnings
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

try:
    from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool  # type: ignore[import-not-found]
except ImportError as e:
    raise ImportError(
        "claude-agent-sdk is required for Claude SDK tools.\n"
        "Install with: pip install thenvoi-sdk[claude_sdk]\n"
        "Or: uv add thenvoi-sdk[claude_sdk]"
    ) from e

from thenvoi.core.exceptions import ThenvoiToolError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
)
from thenvoi.runtime.tools import (
    BASE_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    ToolDefinition,
    iter_tool_definitions,
    mcp_tool_names,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)

# Tool names as constants (MCP naming convention: mcp__{server}__{tool})
THENVOI_CHAT_TOOLS: list[str] = mcp_tool_names(CHAT_TOOL_NAMES)
THENVOI_BASE_TOOLS: list[str] = mcp_tool_names(BASE_TOOL_NAMES)

_THENVOI_TOOLS: list[str] = THENVOI_CHAT_TOOLS

ToolResolver = Callable[[str], AgentToolsProtocol | None]
ParticipantHandlesResolver = Callable[[str], list[str]]
ToolResultHook = Callable[[str, str, Any], Awaitable[None] | None]


def __getattr__(name: str) -> Any:
    if name == "THENVOI_TOOLS":
        warnings.warn(
            "THENVOI_TOOLS is deprecated, use THENVOI_CHAT_TOOLS instead. "
            f"Note: this contains only chat tools ({len(_THENVOI_TOOLS)}). "
            "For all tools including contacts and memory, use "
            "thenvoi.adapters.claude_sdk.THENVOI_ALL_TOOLS.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _THENVOI_TOOLS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _make_result(data: Any) -> dict[str, Any]:
    """Format tool result for Claude SDK MCP responses."""
    return {"content": [{"type": "text", "text": json.dumps(data, default=str)}]}


def _make_error(error: str) -> dict[str, Any]:
    """Format tool error for Claude SDK MCP responses."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"status": "error", "message": error}),
            }
        ],
        "is_error": True,
    }


def _build_sdk_schema(
    input_model: type[Any],
    *,
    include_room_id: bool,
) -> dict[str, Any]:
    """Convert a Pydantic model to Claude SDK JSON schema format."""
    schema: dict[str, Any] = dict(input_model.model_json_schema())
    schema.pop("title", None)

    raw_properties = schema.get("properties")
    properties: dict[str, Any] = (
        dict(raw_properties) if isinstance(raw_properties, dict) else {}
    )
    raw_required = schema.get("required")
    required: list[str] = (
        [item for item in raw_required if isinstance(item, str)]
        if isinstance(raw_required, list)
        else []
    )

    if include_room_id:
        properties = {"room_id": {"type": "string"}, **properties}
        if "room_id" not in required:
            required.insert(0, "room_id")

    schema["type"] = "object"
    schema["properties"] = properties
    if required:
        schema["required"] = required

    return schema


def _format_success_payload(
    tool_name: str,
    call_args: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    """Keep tool result payloads stable across Claude integrations."""
    if tool_name == "thenvoi_send_message":
        return {"status": "success", "message": "Message sent"}
    if tool_name == "thenvoi_send_event":
        return {"status": "success", "message": "Event sent"}
    if tool_name == "thenvoi_add_participant":
        return {
            "status": "success",
            "message": (
                f"Participant '{call_args['identifier']}' added as {call_args['role']}"
            ),
            **result,
        }
    if tool_name == "thenvoi_remove_participant":
        return {
            "status": "success",
            "message": f"Participant '{call_args['identifier']}' removed",
            **result,
        }
    if tool_name == "thenvoi_get_participants":
        participants = result if isinstance(result, list) else []
        # Convert Fern models to dicts for JSON serialization
        serialized = [
            p.model_dump() if hasattr(p, "model_dump") else p for p in participants
        ]
        return {
            "status": "success",
            "participants": serialized,
            "count": len(serialized),
        }
    if tool_name == "thenvoi_create_chatroom":
        return {
            "status": "success",
            "message": "Chat room created",
            "room_id": result,
        }
    # Convert Pydantic models to dicts at serialization boundary
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if isinstance(result, dict):
        return {"status": "success", **result}
    return {"status": "success", "result": result}


async def _maybe_call_tool_result_hook(
    tool_result_hook: ToolResultHook | None,
    tool_name: str,
    room_id: str,
    result: Any,
) -> None:
    if tool_result_hook is None:
        return

    hook_result = tool_result_hook(tool_name, room_id, result)
    if inspect.isawaitable(hook_result):
        await hook_result


def _build_builtin_sdk_tool(
    definition: ToolDefinition,
    *,
    get_tools: ToolResolver,
    include_room_id: bool,
    get_participant_handles: ParticipantHandlesResolver | None,
    tool_result_hook: ToolResultHook | None,
) -> SdkMcpTool[Any]:
    schema = _build_sdk_schema(definition.input_model, include_room_id=include_room_id)

    @tool(
        definition.name,
        definition.input_model.__doc__ or f"Execute {definition.name}",
        schema,
    )
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        room_id = args.get("room_id", "") if include_room_id else ""
        raw_args = {k: v for k, v in args.items() if k != "room_id"}
        tools = get_tools(room_id)
        if tools is None:
            return _make_error(f"No tools available for room {room_id}")

        try:
            call_args = validate_tool_arguments(
                definition.name,
                definition.input_model,
                raw_args,
            )
            method = getattr(tools, definition.method_name)
            result = await method(**call_args)
            await _maybe_call_tool_result_hook(
                tool_result_hook,
                definition.name,
                room_id,
                result,
            )
            return _make_result(
                _format_success_payload(definition.name, call_args, result)
            )
        except (ValueError, ThenvoiToolError) as error:
            if (
                definition.name == "thenvoi_send_message"
                and get_participant_handles is not None
            ):
                available = get_participant_handles(room_id)
                return _make_error(
                    f"{error}. Available handles: {available}. "
                    "Use participant handles from the list."
                )
            return _make_error(str(error))
        except Exception as error:
            logger.exception("%s failed: %s", definition.name, error)
            return _make_error(str(error))

    return handler


def _build_custom_sdk_tool(
    tool_def: CustomToolDef,
    *,
    include_room_id: bool,
) -> SdkMcpTool[Any]:
    input_model, _ = tool_def
    tool_name = get_custom_tool_name(input_model)
    schema = _build_sdk_schema(input_model, include_room_id=include_room_id)

    @tool(
        tool_name,
        input_model.__doc__ or f"Custom tool: {tool_name}",
        schema,
    )
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            tool_args = {k: v for k, v in args.items() if k != "room_id"}
            result = await execute_custom_tool(tool_def, tool_args)
            return _make_result(result)
        except Exception as error:
            logger.exception("Custom tool %s failed: %s", tool_name, error)
            return _make_error(str(error))

    return handler


def build_thenvoi_sdk_tools(
    *,
    tool_definitions: Sequence[ToolDefinition],
    get_tools: ToolResolver,
    include_room_id: bool = True,
    additional_tools: list[CustomToolDef] | None = None,
    get_participant_handles: ParticipantHandlesResolver | None = None,
    tool_result_hook: ToolResultHook | None = None,
) -> list[SdkMcpTool[Any]]:
    """Build Claude SDK MCP tools from central Thenvoi tool definitions."""
    sdk_tools = [
        _build_builtin_sdk_tool(
            definition,
            get_tools=get_tools,
            include_room_id=include_room_id,
            get_participant_handles=get_participant_handles,
            tool_result_hook=tool_result_hook,
        )
        for definition in tool_definitions
    ]

    for custom_tool in additional_tools or []:
        sdk_tools.append(
            _build_custom_sdk_tool(
                custom_tool,
                include_room_id=include_room_id,
            )
        )

    return sdk_tools


def create_thenvoi_sdk_mcp_server(tools: list[SdkMcpTool[Any]]) -> Any:
    """Create a Claude SDK MCP server config for Thenvoi tools."""
    return create_sdk_mcp_server(
        name="thenvoi",
        version="1.0.0",
        tools=tools,
    )


def create_thenvoi_mcp_server(agent: Any) -> Any:
    """
    Create an in-process Claude SDK MCP server for Thenvoi platform tools.

    The returned server uses room-scoped ``AgentTools`` instances resolved from
    the running agent state at tool-call time.
    """
    from thenvoi.runtime.tools import AgentTools

    def get_tools(room_id: str) -> AgentTools:
        executions = agent.runtime.executions if agent.runtime else {}
        execution = executions.get(room_id)
        participants = execution.participants if execution else []
        return AgentTools(room_id, agent.link.rest, participants)

    def get_participant_handles(room_id: str) -> list[str]:
        executions = agent.runtime.executions if agent.runtime else {}
        execution = executions.get(room_id)
        participants = execution.participants if execution else []
        return [p.get("handle", "") for p in participants if p.get("handle")]

    def tool_result_hook(tool_name: str, room_id: str, result: Any) -> None:
        executions = agent.runtime.executions if agent.runtime else {}
        execution = executions.get(room_id)
        if execution is None:
            return

        if tool_name == "thenvoi_add_participant" and isinstance(result, dict):
            participant_id = result.get("id")
            participant_name = result.get("name")
            if participant_id and participant_name:
                execution.add_participant(
                    {
                        "id": participant_id,
                        "name": participant_name,
                        "type": "Agent",
                    }
                )

        if tool_name == "thenvoi_remove_participant" and isinstance(result, dict):
            participant_id = result.get("id")
            if participant_id:
                execution.remove_participant(participant_id)

    tool_definitions = [
        definition
        for definition in iter_tool_definitions(include_memory=False)
        if definition.name in BASE_TOOL_NAMES
    ]
    sdk_tools = build_thenvoi_sdk_tools(
        tool_definitions=tool_definitions,
        get_tools=get_tools,
        get_participant_handles=get_participant_handles,
        tool_result_hook=tool_result_hook,
    )
    server = create_thenvoi_sdk_mcp_server(sdk_tools)

    logger.info(
        "Thenvoi MCP SDK server created with %s real tools",
        len(sdk_tools),
    )

    return server
