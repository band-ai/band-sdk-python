"""MCP engine behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.integrations.mcp.engine import (
    SendEventWideInput,
)


class TestSendEventWideInput:
    def test_advertises_all_five_message_types(self) -> None:
        schema = SendEventWideInput.model_json_schema()
        assert set(schema["properties"]["message_type"]["enum"]) == {
            "tool_call",
            "tool_result",
            "thought",
            "error",
            "task",
        }

    def test_accepts_tool_call_and_tool_result(self) -> None:
        for message_type in ("tool_call", "tool_result"):
            validated = SendEventWideInput.model_validate(
                {"content": "x", "message_type": message_type}
            )
            assert validated.message_type == message_type

    def test_rejects_content_with_no_visible_characters(self) -> None:
        """The independent model still carries SendEventInput's content rule."""
        with pytest.raises(ValidationError, match="content"):
            SendEventWideInput.model_validate(
                {"content": "   ", "message_type": "tool_result"}
            )
