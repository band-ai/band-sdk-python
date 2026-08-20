"""Bridge fixes: MCP-shaped results pass through; declared room_id survives."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from band.integrations.claude_sdk.tools import (
    _build_custom_sdk_tool,
    _make_result,
)


class TestMakeResultPassthrough:
    def test_plain_values_still_wrap_as_one_text_block(self) -> None:
        result = _make_result({"status": "ok"})
        assert result["content"][0]["type"] == "text"
        assert json.loads(result["content"][0]["text"]) == {"status": "ok"}

    def test_mcp_shaped_content_passes_through_untouched(self) -> None:
        vision = {
            "content": [
                {"type": "image", "data": "aGk=", "mimeType": "image/png"},
                {"type": "text", "text": "the image above"},
            ]
        }
        assert _make_result(vision) is vision

    def test_content_key_holding_non_blocks_is_not_mistaken_for_mcp(self) -> None:
        lookalike = {"content": ["just", "strings"]}
        result = _make_result(lookalike)
        assert result["content"][0]["type"] == "text"

    def test_an_empty_content_list_is_not_mistaken_for_mcp(self) -> None:
        # A payload that merely happens to carry a "content" key. Passed
        # through, it reaches the model as a tool result with nothing in it
        # and everything else the payload said is lost.
        lookalike = {"content": [], "rows": 0}
        result = _make_result(lookalike)
        assert json.loads(result["content"][0]["text"]) == lookalike


class EchoRoomInput(BaseModel):
    """Echo tool that declares room_id as a real field."""

    room_id: str = Field(description="The current chat room id")


class EchoPlainInput(BaseModel):
    """Echo tool without a room_id field."""

    label: str = Field(default="x")


class TestCustomToolRoomIdStrip:
    @pytest.mark.asyncio
    async def test_declared_room_id_reaches_the_handler(self) -> None:
        seen: dict[str, str] = {}

        async def handler(inp: EchoRoomInput) -> str:
            seen["room_id"] = inp.room_id
            return "ok"

        sdk_tool = _build_custom_sdk_tool(
            (EchoRoomInput, handler), include_room_id=True
        )
        result = await sdk_tool.handler({"room_id": "room-7"})
        assert seen["room_id"] == "room-7"
        assert "is_error" not in result

    @pytest.mark.asyncio
    async def test_undeclared_room_id_is_still_stripped(self) -> None:
        async def handler(inp: EchoPlainInput) -> str:
            return f"label={inp.label}"

        sdk_tool = _build_custom_sdk_tool(
            (EchoPlainInput, handler), include_room_id=True
        )
        result = await sdk_tool.handler({"room_id": "room-7", "label": "y"})
        assert "is_error" not in result
        assert "label=y" in result["content"][0]["text"]
