"""Completion permissions for the trusted Cursor approval-test peer."""

from __future__ import annotations

from pydantic import ValidationError

from band.adapters.cursor_acp import CursorACPAdapter
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.client_runtime import ALLOW_ONCE_KIND, option_id_of_kind
from band.runtime.tools import BandTool
from band.runtime.tools.inputs.chat import SendMessageInput


class CursorApprovalAdapter(CursorACPAdapter):
    """Keep native commands manual while allowing a room-bound completion reply."""

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
