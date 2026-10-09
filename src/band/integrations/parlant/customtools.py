"""A Band custom tool (``(InputModel, handler)``) as a Parlant tool."""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, cast

import parlant.sdk as p
from parlant.core.tools import ToolContext, ToolParameterDescriptor, ToolResult

from band.integrations.parlant.customschema import describe_custom_tool
from band.integrations.parlant.guard import guard_failures
from band.integrations.parlant.sessiontools import (
    CONTEXT_PARAMETER,
    require_session_tools,
)
from band.runtime.custom_tools import CustomToolDef, execute_custom_tool
from band.runtime.tools import tool_result_text


def build_custom_tool(tool_def: CustomToolDef) -> Any:
    """A guarded Parlant ``ToolEntry`` that runs *tool_def* for the calling room."""
    input_model, _ = tool_def
    described = describe_custom_tool(input_model)

    async def run(context: ToolContext, **arguments: Any) -> ToolResult:
        tools = require_session_tools(context)
        result = await execute_custom_tool(tool_def, arguments, turn=tools.turn)
        return ToolResult(data=tool_result_text(result))

    run.__name__ = described.name
    run.__doc__ = described.description
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                CONTEXT_PARAMETER,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=ToolContext,
            ),
            *(field.parameter for field in described.fields),
        ],
        return_annotation=ToolResult,
    )
    entry = p.tool(
        guard_failures(run, failure=f"running {described.name}", mention_hints=False)
    )
    # p.tool described the all-string signature Parlant casts by; advertise
    # the input model's own types instead, keeping p.tool's options.
    parameters = {
        field.parameter.name: (
            cast(ToolParameterDescriptor, field.descriptor),
            entry.tool.parameters[field.parameter.name][1],
        )
        for field in described.fields
    }
    return dataclasses.replace(
        entry, tool=dataclasses.replace(entry.tool, parameters=parameters)
    )
