"""MCP engine peers support."""

from __future__ import annotations

from typing import Any

from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    build_engine,
    build_tool_registration,
    pin_existing_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.conftest import FakeHumanTools

BOB_PEER = {
    "id": "bob-id",
    "handle": "bob",
    "name": "Bob",
    "type": "User",
    "is_contact": False,
    "source": "registry",
    "online": True,
}


ALICE_PARTICIPANT = {
    "id": "u1",
    "name": "Alice",
    "handle": "@alice",
    "role": "member",
    "status": "active",
    "type": "User",
}


def _agent_resolver(fake: FakeAgentTools) -> EmbeddedResolver:
    """A resolver that always returns the same room-scoped fake -- mirrors
    the embedded door's uniform routing for a single-room test."""
    return EmbeddedResolver(get_tools=lambda chat_id: fake)


class ImageFileAgentTools(FakeAgentTools):
    """A room whose one file is a previewable image.

    ``FakeAgentTools.read_room_file`` deliberately never fabricates bytes
    (it returns a description-only result) -- this override supplies the
    real MCP image-content shape ``AgentTools.read_room_file`` produces for
    a small previewable image, so the image-passthrough test below exercises
    the engine against that exact shape without a live platform.
    """

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "image",
                    "data": "ZmFrZS1pbWFnZS1ieXRlcw==",
                    "mimeType": "image/png",
                }
            ]
        }


class HumanResolver:
    """Human-surface dispatch needs no per-room routing -- chat_id (if any)
    stays in ``arguments`` and is passed straight to the fake's method."""

    def __init__(self, human_tools: FakeHumanTools) -> None:
        self._human_tools = human_tools

    async def invoke(self, definition, chat_id, arguments):
        method = getattr(self._human_tools, definition.method_name)
        return await method(**arguments)


def _human_send_message_engine(fake: FakeHumanTools, *, pinned: bool) -> Any:
    definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
    input_model = (
        pin_existing_chat_id(definition.input_model)
        if pinned
        else definition.input_model
    )
    registration = build_tool_registration(
        definition,
        input_model,
        resolver=HumanResolver(fake),
        strip_chat_id=False,
        pinned_room_id="chat-1" if pinned else None,
    )
    return build_engine(EngineSpec(name="test-human", tools=(registration,)))
