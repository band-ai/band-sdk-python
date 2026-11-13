"""The dynamic tools a Codex thread is offered, and running the calls it makes."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from band.adapters.codex.narration import tool_event
from band.adapters.codex.rpc import CodexClientProtocol
from band.core.exceptions import HOST_CONTROL_ERRORS
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, MessageType
from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tool_to_openai_schema,
    find_custom_tool,
    format_validation_error,
    run_custom_tool,
)
from band.runtime.tools import (
    get_band_tool_category,
    image_block_placeholder,
    is_image_passthrough_result,
    redact_tool_call_args,
)
from band.runtime.turn import declined_turn_refusal

logger = logging.getLogger(__name__)

UNOWNED_TOOL_CALL_TEXT = (
    "No Band turn started this Codex turn, so Band tools are unavailable to it."
)


def image_content_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """An image tool result as Codex app-server ``inputImage`` content items.

    Inlined as a data: URI -- the protocol's ``imageUrl`` is a bare string with
    no documented inline-vs-http distinction.
    """
    return [
        {
            "type": "inputImage",
            "imageUrl": f"data:{block['mimeType']};base64,{block['data']}",
        }
        for block in result["content"]
    ]


def text_item(text: str) -> dict[str, Any]:
    return {"type": "inputText", "text": text}


def tool_response(items: list[dict[str, Any]], *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response."""
    return {"contentItems": items, "success": success}


def text_tool_result(text: str, *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response carrying one text item."""
    return tool_response([text_item(text)], success=success)


def tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(tool.get("name")): tool for tool in tools}


def _tool_spec(name: str, description: Any, input_schema: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": str(description or ""),
        "inputSchema": input_schema
        if isinstance(input_schema, dict)
        else {"type": "object", "properties": {}},
    }


def _band_tool_spec(schema: Any) -> dict[str, Any] | None:
    """A Band tool schema, OpenAI- or Anthropic-shaped, as a dynamic tool."""
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "function":
        function = schema.get("function")
        if not isinstance(function, dict):
            return None
        name, description = function.get("name"), function.get("description")
        input_schema = function.get("parameters")
    else:
        name, description = schema.get("name"), schema.get("description")
        input_schema = schema.get("inputSchema") or schema.get("input_schema")
    if not isinstance(name, str) or not name:
        return None
    return _tool_spec(name, description, input_schema)


def _custom_tool_spec(tool: CustomToolDef) -> dict[str, Any] | None:
    function = custom_tool_to_openai_schema(tool[0]).get("function", {})
    name = function.get("name", "")
    if not name:
        return None
    return _tool_spec(name, function.get("description"), function.get("parameters"))


def _unseen(
    specs: Iterable[dict[str, Any] | None], seen: set[str]
) -> list[dict[str, Any]]:
    """The specs whose names are not yet in ``seen``, recording each kept name."""
    kept: list[dict[str, Any]] = []
    for spec in specs:
        if spec is None:
            continue
        name = spec.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        kept.append(spec)
    return kept


def dynamic_tools(
    band_schemas: Iterable[Any],
    features: AdapterFeatures,
    *,
    additional: Iterable[Any],
    custom_tools: Iterable[CustomToolDef],
) -> list[dict[str, Any]]:
    """The dynamic tools offered to a thread: Band's, as ``features`` allow,
    then the configured extras and the custom tools, first name winning.

    A Band tool ``features`` filters out still claims its name.
    """
    seen: set[str] = set()
    band = filter_tool_schemas(
        _unseen(map(_band_tool_spec, band_schemas), seen),
        features,
        get_name=lambda schema: schema["name"],
        get_category=lambda schema: get_band_tool_category(schema["name"]),
    )
    extras = (tool for tool in additional if isinstance(tool, dict))
    return [
        *band,
        *_unseen(extras, seen),
        *_unseen(map(_custom_tool_spec, custom_tools), seen),
    ]


@dataclass(frozen=True)
class DynamicToolCall:
    """One ``item/tool/call`` request."""

    name: str
    arguments: dict[str, Any]
    call_id: str

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> DynamicToolCall:
        arguments = params.get("arguments")
        return cls(
            name=str(params.get("tool") or ""),
            arguments=arguments if isinstance(arguments, dict) else {},
            call_id=str(params.get("callId") or ""),
        )


async def answer_tool_call(
    client: CodexClientProtocol,
    tools: AgentToolsProtocol,
    *,
    request_id: int | str,
    call: DynamicToolCall,
    custom_tools: list[CustomToolDef],
    report: bool,
) -> None:
    """Run ``call`` and answer Codex, reporting the call to the room if asked."""
    if report:
        args = redact_tool_call_args(call.name, call.arguments)
        await tool_event(tools, MessageType.TOOL_CALL, call.name, call.call_id, args)
    response, output = await _run(call, tools, custom_tools)
    await client.respond(request_id, response)
    if report:
        await tool_event(
            tools, MessageType.TOOL_RESULT, call.name, call.call_id, output
        )


async def _run(
    call: DynamicToolCall,
    tools: AgentToolsProtocol,
    custom_tools: list[CustomToolDef],
) -> tuple[dict[str, Any], str]:
    """Codex's response to ``call`` and the output the room is shown.

    Either kind of tool can fail without raising (bad args, an API error, a
    failure value) via ``ok=False``, which reaches Codex as a failed call.
    """
    if refusal := declined_turn_refusal(tools.turn):
        return text_tool_result(refusal, success=False), refusal
    try:
        if custom_tool := find_custom_tool(custom_tools, call.name):
            outcome = await run_custom_tool(custom_tool, call.arguments, tools=tools)
        else:
            outcome = await tools.execute_tool_call_structured(
                call.name, call.arguments
            )
    except HOST_CONTROL_ERRORS:
        raise
    except ValidationError as exc:
        logger.error("Validation error for tool %s: %s", call.name, exc)
        error_text = (
            f"Invalid arguments for {call.name}: {format_validation_error(exc)}"
        )
        return text_tool_result(error_text, success=False), error_text
    except Exception as exc:
        logger.exception("Tool execution failed for %s", call.name)
        error_text = f"Error: {exc}"
        return text_tool_result(error_text, success=False), error_text

    result, success = outcome.value, outcome.ok
    if success and is_image_passthrough_result(call.name, result):
        items = image_content_items(result)
        return tool_response(items, success=True), image_block_placeholder(len(items))
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    return text_tool_result(text, success=success), text
