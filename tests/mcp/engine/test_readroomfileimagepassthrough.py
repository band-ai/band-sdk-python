"""MCP engine behavior."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import (
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.peers import ImageFileAgentTools, _agent_resolver


class TestReadRoomFileImagePassthrough:
    """band_read_room_file's image branch must reach the MCP client as a
    real image content block, not get json.dumps'd into text like every
    other tool result (see is_mcp_content_result/_mcp_content_blocks)."""

    async def test_image_result_arrives_as_image_content_block(self) -> None:
        fake = ImageFileAgentTools(room_id="room-1")
        resolver = _agent_resolver(fake)
        definition = TOOL_DEFINITIONS["band_read_room_file"]
        registration = build_tool_registration(
            definition,
            extend_with_chat_id(definition.input_model, None),
            resolver=resolver,
            strip_chat_id=True,
        )
        mcp = build_engine(EngineSpec(name="test-image", tools=(registration,)))

        async with create_connected_server_and_client_session(mcp) as session:
            result = await session.call_tool(
                "band_read_room_file", {"chat_id": "room-1", "file_id": "f1"}
            )

        assert not result.isError, result.content
        assert len(result.content) == 1
        block = result.content[0]
        assert block.type == "image"
        assert block.data == "ZmFrZS1pbWFnZS1ieXRlcw=="
        assert block.mimeType == "image/png"

    async def test_non_image_result_still_arrives_as_text(
        self, agent_session_factory
    ) -> None:
        """A description-only (non-image) read_room_file result keeps the
        ordinary text-content wire shape -- structured_output=False only
        changes how the *unstructured* content is built, not which results
        qualify for the image branch."""
        fake = FakeAgentTools(
            room_id="room-1",
            files=[
                {
                    "id": "f1",
                    "name": "notes.txt",
                    "content_type": "text/plain",
                    "bytes": 12,
                    "sha256": "a" * 64,
                    "has_thumb": False,
                    "available": True,
                }
            ],
        )
        mcp = await agent_session_factory(
            fake, definitions=[TOOL_DEFINITIONS["band_read_room_file"]]
        )

        async with create_connected_server_and_client_session(mcp) as session:
            result = await session.call_tool(
                "band_read_room_file", {"chat_id": "room-1", "file_id": "f1"}
            )

        assert not result.isError, result.content
        assert result.content[0].type == "text"
        payload = json.loads(result.content[0].text)
        assert payload["description"].startswith("Fake file 'notes.txt'")
