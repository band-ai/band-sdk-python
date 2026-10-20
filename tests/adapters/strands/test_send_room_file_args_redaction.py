from __future__ import annotations

import json

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
)
from band.core.types import (
    Capability,
    Emit,
)
from band.testing import (
    ScriptedStrandsModel,
    ToolTurn,
)
from tests.adapters.strands.kit import _run_message


class TestSendRoomFileArgsRedaction:
    @pytest.mark.asyncio
    async def test_tool_call_event_redacts_content_not_raw_bytes(self, tools):
        """band_send_room_file's tool_call event must report a bounded
        placeholder for content, not the raw file bytes -- generic ARGS
        reporting has no idea this one tool's content argument can carry up
        to MAX_SEND_CONTENT_BYTES of real file data."""
        adapter = StrandsAdapter(
            llm=ScriptedStrandsModel(
                (
                    ToolTurn(
                        "band_send_room_file",
                        {"content": "raw file bytes", "filename": "notes.txt"},
                    ),
                )
            ),
            capabilities=Capability.FILES,
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools)

        tool_calls = [
            json.loads(e["content"])
            for e in tools.events_sent
            if e["message_type"] == "tool_call"
        ]
        [send_room_file_call] = [
            c for c in tool_calls if c["name"] == "band_send_room_file"
        ]
        assert send_room_file_call["args"]["content"] == "<14 byte file content>"
