"""OnCleanup behavior for Gemini."""

from __future__ import annotations

import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_removes_room_history(self):
        adapter = GeminiAdapter()
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_twice_is_idempotent(self):
        adapter = GeminiAdapter()
        adapter._message_history["room-1"] = []
        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise
        assert "room-1" not in adapter._message_history

    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = GeminiAdapter()
        await adapter.on_cleanup("nonexistent-room")  # Should not raise
        assert "nonexistent-room" not in adapter._message_history

    def test_trim_history_caps_at_max(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=5))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text=f"msg-{i}")])
            for i in range(10)
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 5
        # Keeps the most recent messages
        assert adapter._message_history["room-1"][0].parts[0].text == "msg-5"

    def test_trim_history_noop_when_under_limit(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=50))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="hi")])
        ]
        adapter._trim_history("room-1")
        assert len(adapter._message_history["room-1"]) == 1

    def test_trim_history_drops_leading_model_entry(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=3))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="msg-0")]),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-0")]),
            types.Content(role="user", parts=[types.Part.from_text(text="msg-1")]),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-1")]),
        ]

        adapter._trim_history("room-1")

        trimmed = adapter._message_history["room-1"]
        assert len(trimmed) == 2
        assert trimmed[0].role == "user"
        assert trimmed[0].parts[0].text == "msg-1"
        assert trimmed[1].role == "model"
        assert trimmed[1].parts[0].text == "reply-1"

    def test_trim_history_strips_orphaned_leading_tool_response_parts(self):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_history_messages=3))
        adapter._message_history["room-1"] = [
            types.Content(role="user", parts=[types.Part.from_text(text="msg-0")]),
            types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(
                        name="band_send_message",
                        args={"content": "hello"},
                    )
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="tc_1",
                            name="band_send_message",
                            response={"output": {"status": "sent"}},
                        )
                    ),
                    types.Part.from_text(text="[Alice]: follow-up"),
                ],
            ),
            types.Content(role="model", parts=[types.Part.from_text(text="reply-1")]),
        ]

        adapter._trim_history("room-1")

        trimmed = adapter._message_history["room-1"]
        assert len(trimmed) == 2
        assert trimmed[0].role == "user"
        assert len(trimmed[0].parts) == 1
        assert trimmed[0].parts[0].function_response is None
        assert trimmed[0].parts[0].text == "[Alice]: follow-up"
        assert trimmed[1].role == "model"
        assert trimmed[1].parts[0].text == "reply-1"

    @pytest.mark.asyncio
    async def test_cleanup_before_any_messages(self):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        await adapter.on_cleanup("room-never-used")  # Should not raise
