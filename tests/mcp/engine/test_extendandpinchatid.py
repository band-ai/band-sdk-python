"""MCP engine behavior."""

from __future__ import annotations

from band.integrations.mcp.engine import (
    extend_with_chat_id,
    pin_existing_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS


class TestExtendAndPinChatId:
    def test_extend_with_chat_id_accepts_room_id_alias(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_message"]
        extended = extend_with_chat_id(definition.input_model, None)

        via_chat_id = extended.model_validate(
            {"content": "hi", "mentions": ["@x"], "chat_id": "r1"}
        )
        via_room_id = extended.model_validate(
            {"content": "hi", "mentions": ["@x"], "room_id": "r2"}
        )
        assert via_chat_id.chat_id == "r1"
        assert via_room_id.chat_id == "r2"

    def test_extend_with_chat_id_pinned_hides_field_from_schema(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_message"]
        pinned = extend_with_chat_id(definition.input_model, "r_pinned")
        schema = pinned.model_json_schema()
        assert "chat_id" not in schema.get("properties", {})

    def test_pin_existing_chat_id_hides_field_from_schema(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
        pinned = pin_existing_chat_id(definition.input_model)
        schema = pinned.model_json_schema()
        assert "chat_id" not in schema.get("properties", {})
