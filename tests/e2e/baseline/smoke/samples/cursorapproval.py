"""Completion permissions for the trusted Cursor approval-test peer."""

from __future__ import annotations

import json

from acp.schema import ContentToolCallContent, TextContent, ToolCallUpdate
from pydantic import ValidationError

from band.adapters.cursor_acp import CursorACPAdapter
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.client_runtime import (
    ALLOW_ONCE_KIND,
    PermissionHandler,
    option_id_of_kind,
)
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool, mcp_tool_spelling
from band.runtime.tools.inputs.chat import SendMessageInput


class CursorApprovalAdapter(CursorACPAdapter):
    """Keep native commands manual while allowing a room-bound completion reply."""

    def _make_permission_handler(
        self, emitter: RoomTurnEmitter, room_id: str
    ) -> PermissionHandler:
        inherited = super()._make_permission_handler(emitter, room_id)

        async def handler(
            options: object,
            session_id: str,
            tool_call: object,
            **kwargs: object,
        ) -> dict[str, object]:
            return await inherited(
                options, session_id, cursor_reply_input(tool_call), **kwargs
            )

        return handler

    async def _resolve_cursor_permission(
        self, request: ACPPermissionRequest
    ) -> str | None:
        call = request.tool_call
        once = option_id_of_kind(request.options, ALLOW_ONCE_KIND)
        if (
            once is not None
            and call.name == BandTool.SEND_MESSAGE
            and call.name in self._own_tool_names
            and self._active_turn_for(request.room_id, request.session_id) is not None
        ):
            try:
                SendMessageInput.model_validate(call.arguments)
            except ValidationError:
                pass
            else:
                return once
        return await super()._resolve_cursor_permission(request)


def cursor_reply_input(tool_call: object) -> object:
    """Cursor's pinned permission frame carries reply args only as display JSON."""
    title = (
        f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)}: "
        f"{BandTool.SEND_MESSAGE}"
    )
    if (
        not isinstance(tool_call, ToolCallUpdate)
        or tool_call.raw_input is not None
        or tool_call.title != title
        or tool_call.kind != "other"
        or not tool_call.content
        or len(tool_call.content) != 1
    ):
        return tool_call
    block = tool_call.content[0]
    if not isinstance(block, ContentToolCallContent) or not isinstance(
        block.content, TextContent
    ):
        return tool_call
    lines = block.content.text.splitlines()
    if len(lines) < 3 or lines[0] != "```json" or lines[-1] != "```":
        return tool_call
    try:
        arguments = json.loads("\n".join(lines[1:-1]))
    except json.JSONDecodeError:
        return tool_call
    if not isinstance(arguments, dict):
        return tool_call
    return tool_call.model_copy(
        update={
            "raw_input": {
                "providerIdentifier": BAND_MCP_SERVER_NAME,
                "toolName": BandTool.SEND_MESSAGE,
                "args": arguments,
            }
        }
    )
