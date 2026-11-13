"""MCP humandispatch behavior."""

from __future__ import annotations

from mcp.shared.memory import create_connected_server_and_client_session

from tests.mcp.conftest import FakeHumanTools
from tests.mcp.engine.peers import _human_send_message_engine
from tests.mcp.engine.protocol import _call, _list_tool


class TestHumanDispatch:
    async def test_human_room_bound_unpinned_keeps_chat_id_as_real_argument(
        self,
    ) -> None:
        fake = FakeHumanTools(
            chats=[{"id": "chat-1"}],
            chat_participants={"chat-1": [{"id": "p1", "name": "Alice"}]},
        )
        mcp = _human_send_message_engine(fake, pinned=False)

        async with create_connected_server_and_client_session(mcp) as session:
            tool = await _list_tool(session, "band_send_my_chat_message")
            assert "chat_id" in tool.inputSchema["properties"]

            await _call(
                session,
                "band_send_my_chat_message",
                chat_id="chat-1",
                content="hi",
                recipients="Alice",
            )
            assert fake.messages_sent[0]["chat_id"] == "chat-1"

    async def test_human_room_bound_pinned_injects_and_hides_chat_id(self) -> None:
        fake = FakeHumanTools(
            chats=[{"id": "chat-1"}],
            chat_participants={"chat-1": [{"id": "p1", "name": "Alice"}]},
        )
        mcp = _human_send_message_engine(fake, pinned=True)

        async with create_connected_server_and_client_session(mcp) as session:
            tool = await _list_tool(session, "band_send_my_chat_message")
            assert "chat_id" not in tool.inputSchema["properties"]

            await _call(
                session,
                "band_send_my_chat_message",
                content="hi",
                recipients="Alice",
            )
            assert fake.messages_sent[0]["chat_id"] == "chat-1"
