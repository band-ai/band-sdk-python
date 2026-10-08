"""A Band custom tool (``(InputModel, handler)``) as a Parlant tool."""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, cast

import parlant.sdk as p
from parlant.core.tools import ToolContext, ToolParameterDescriptor, ToolResult

from band.integrations.parlant.customschema import CONTEXT_PARAMETER, custom_tool_fields
from band.integrations.parlant.guard import guard_failures
from band.integrations.parlant.sessiontools import require_session_tools
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
)
from band.runtime.tools import tool_result_text


def build_custom_tool(tool_def: CustomToolDef) -> Any:
    """A guarded Parlant ``ToolEntry`` that runs *tool_def* for the calling room."""
    input_model, _ = tool_def
    name = get_custom_tool_name(input_model)
    fields = custom_tool_fields(input_model)

    async def run(context: ToolContext, **arguments: Any) -> ToolResult:
        tools = require_session_tools(context)
        result = await execute_custom_tool(tool_def, arguments, turn=tools.turn)
        return ToolResult(data=tool_result_text(result))

    run.__name__ = name
    run.__doc__ = input_model.__doc__ or ""
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                CONTEXT_PARAMETER,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=ToolContext,
            ),
            *(field.parameter for field in fields),
        ],
        return_annotation=ToolResult,
    )
    entry = p.tool(guard_failures(run, failure=f"running {name}", mention_hints=False))
    # p.tool described the all-string signature Parlant casts by; advertise
    # the input model's own types instead, keeping p.tool's options.
    parameters = {
        field.parameter.name: (
            cast(ToolParameterDescriptor, field.descriptor),
            entry.tool.parameters[field.parameter.name][1],
        )
        for field in fields
    }
    return dataclasses.replace(
        entry, tool=dataclasses.replace(entry.tool, parameters=parameters)
    )
