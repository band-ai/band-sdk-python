"""Codex adapter: read room file image passthrough."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.tools import ToolCallOutcome
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    tool_call_request,
    turn_completed,
)


class TestReadRoomFileImagePassthrough:
    @pytest.mark.asyncio
    async def test_image_result_becomes_input_image_content_item(self) -> None:
        class ImageTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                return ToolCallOutcome(
                    value={
                        "content": [
                            {
                                "type": "image",
                                "data": "ZmFrZQ==",
                                "mimeType": "image/png",
                            }
                        ]
                    },
                    ok=True,
                )

        turn = await run_codex_turn(
            events=[
                tool_call_request(42, "band_read_room_file", {"file_id": "f1"}),
                turn_completed(),
            ],
            tools=ImageTools(),
        )

        response_id, response_payload = turn.tool_response
        assert response_id == 42
        assert response_payload["success"] is True
        assert turn.content_items == [
            {"type": "inputImage", "imageUrl": "data:image/png;base64,ZmFrZQ=="}
        ]

    @pytest.mark.asyncio
    async def test_non_image_result_stays_input_text(self) -> None:
        turn = await run_codex_turn(
            events=[
                tool_call_request(42, "band_read_room_file", {"file_id": "f1"}),
                turn_completed(),
            ]
        )

        assert turn.content_items[0]["type"] == "inputText"
