from __future__ import annotations

from typing import Any

from band.runtime.tools import BandTool
from tests.adapters.copilot_sdk.fakes import (
    ToolSchemaFakeTools,
)


class CreateChatroomFakeTools(ToolSchemaFakeTools):
    """Also bridges band_create_chatroom, a real-work (ACT) tool."""

    def get_openai_tool_schemas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            *super().get_openai_tool_schemas(**kwargs),
            {
                "type": "function",
                "function": {
                    "name": BandTool.CREATE_CHATROOM,
                    "description": "Create a chat room",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
