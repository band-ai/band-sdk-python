"""Room task board tools — gated behind ``Capability.TASKS``."""

from __future__ import annotations

import json
from typing import Literal, cast

from parlant.core.tools import ToolContext, ToolResult

from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.integrations.parlant.bandtool import band_tool
from band.integrations.parlant.sessiontools import require_session_tools
from band.runtime.tools import (
    BandTool,
    platform_args_schema,
    serialize_tool_result,
    validate_tool_arguments,
)


@band_tool("listing tasks")
async def band_list_tasks(
    context: ToolContext,
    state: TaskListState | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    data = serialize_tool_result(
        await tools.list_tasks(state=state, cursor=cursor, limit=limit)
    )
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("creating task '{subject}'")
async def band_create_task(
    context: ToolContext,
    subject: str,
    detail: str | None = None,
    supersedes_id: str | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    data = serialize_tool_result(
        await tools.create_task(subject, detail=detail, supersedes_id=supersedes_id)
    )
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("getting task '{id}'")
async def band_get_task(
    context: ToolContext,
    id: str,
    # str, not Literal["history"] | None: Parlant's own schema builder
    # raises at registration time for a bare Literal type.
    include: str | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    args = validate_tool_arguments(
        BandTool.GET_TASK,
        platform_args_schema(BandTool.GET_TASK),
        {"id": id, "include": include},
    )
    data = serialize_tool_result(await tools.get_task(**args))
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("updating task '{id}'")
async def band_update_task(
    context: ToolContext,
    id: str,
    status: TaskAssignmentStatus | None = None,
    active_form: str | None = None,
    comment: str | None = None,
    subject: str | None = None,
    detail: str | None = None,
    state: TaskLifecycleState | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    args = validate_tool_arguments(
        BandTool.UPDATE_TASK,
        platform_args_schema(BandTool.UPDATE_TASK),
        {
            "id": id,
            "status": status,
            "active_form": active_form,
            "comment": comment,
            "subject": subject,
            "detail": detail,
            "state": state,
        },
    )
    data = serialize_tool_result(await tools.update_task(**args))
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("getting task history for '{id}'")
async def band_get_task_history(
    context: ToolContext,
    id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    args = validate_tool_arguments(
        BandTool.GET_TASK_HISTORY,
        platform_args_schema(BandTool.GET_TASK_HISTORY),
        {"id": id, "cursor": cursor, "limit": limit},
    )
    data = serialize_tool_result(await tools.get_task_history(**args))
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("getting board")
async def band_get_board(
    context: ToolContext,
    # See band_get_task's `include` for why this is str, not Literal.
    include: str | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    data = serialize_tool_result(
        await tools.get_board(include=cast(Literal["history"] | None, include))
    )
    return ToolResult(data=json.dumps(data, default=str))


@band_tool("setting board")
async def band_set_board(
    context: ToolContext,
    goal_title: str | None = None,
    goal_summary: str | None = None,
) -> ToolResult:
    tools = require_session_tools(context)
    data = serialize_tool_result(
        await tools.set_board(goal_title=goal_title, goal_summary=goal_summary)
    )
    return ToolResult(data=json.dumps(data, default=str))


TOOLS = (
    band_list_tasks,
    band_create_task,
    band_get_task,
    band_update_task,
    band_get_task_history,
    band_get_board,
    band_set_board,
)
