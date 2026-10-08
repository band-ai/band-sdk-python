"""Tests for the Parlant chat tools."""

from __future__ import annotations

import pytest

from band.integrations.parlant.tools import set_session_tools
from band.testing import FakeAgentTools

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


class TestChatTools:
    """Drives the real chat tools built by ``create_parlant_tools``."""

    @pytest.mark.asyncio
    async def test_send_message_calls_tools_send_message(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_message with parsed mentions."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello world", "Alice, Bob")

        mock_tools.send_message.assert_called_once_with("Hello world", ["Alice", "Bob"])
        assert "Message sent to Alice, Bob" in result.data

    @pytest.mark.asyncio
    async def test_send_message_settles_the_turn_reply(
        self, parlant_tools, mock_context
    ):
        """The adapter reads the turn, so it won't duplicate the tool's reply."""
        tools = FakeAgentTools()
        set_session_tools(mock_context.session_id, tools)

        send_message = parlant_tools["band_send_message"]
        await send_message(mock_context, "Hello", "Alice")

        assert tools.turn.replied

    @pytest.mark.asyncio
    async def test_no_reply_calls_tools_no_reply(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should end the turn locally without posting to the room."""
        set_session_tools(mock_context.session_id, mock_tools)

        no_reply = parlant_tools["band_no_reply"]
        result = await no_reply(mock_context, reason="addressed elsewhere")

        mock_tools.no_reply.assert_awaited_once_with("addressed elsewhere")
        assert "No reply sent" in result.data

    @pytest.mark.asyncio
    async def test_no_reply_omits_a_blank_reason(
        self, parlant_tools, mock_tools, mock_context
    ):
        set_session_tools(mock_context.session_id, mock_tools)

        no_reply = parlant_tools["band_no_reply"]
        await no_reply(mock_context, reason="")

        mock_tools.no_reply.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_no_reply_settles_the_turn_reply(self, parlant_tools, mock_context):
        """A decline settles the turn, so the adapter posts nothing of its own."""
        tools = FakeAgentTools()
        set_session_tools(mock_context.session_id, tools)

        no_reply = parlant_tools["band_no_reply"]
        await no_reply(mock_context, reason="")

        assert tools.turn.replied

    @pytest.mark.asyncio
    async def test_send_message_requires_mentions(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error when no mentions provided."""
        mock_tools.agent_id = "self"
        mock_tools.participants = [
            {"id": "user-1", "handle": "@alice"},
            {"id": "self", "handle": "@self"},
        ]
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "")

        assert "At least one mention is required" in result.data
        assert "@alice" in result.data
        assert "@self" not in result.data

    @pytest.mark.asyncio
    async def test_send_event_calls_tools_send_event(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_event with correct parameters."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["band_send_event"]
        result = await send_event(mock_context, "Thinking...", "thought")

        mock_tools.send_event.assert_called_once_with("Thinking...", "thought", None)
        assert "Event (thought) sent successfully" in result.data

    @pytest.mark.asyncio
    async def test_send_event_validates_message_type(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should reject invalid message types."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_event = parlant_tools["band_send_event"]
        result = await send_event(mock_context, "Test", "invalid_type")

        assert "Invalid message_type" in result.data

    @pytest.mark.asyncio
    async def test_add_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.add_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        add_participant = parlant_tools["band_add_participant"]
        result = await add_participant(mock_context, "Research Agent")

        mock_tools.add_participant.assert_called_once_with("Research Agent", "member")
        assert "Successfully added 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_remove_participant_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.remove_participant."""
        set_session_tools(mock_context.session_id, mock_tools)

        remove_participant = parlant_tools["band_remove_participant"]
        result = await remove_participant(mock_context, "Research Agent")

        mock_tools.remove_participant.assert_called_once_with("Research Agent")
        assert "Successfully removed 'Research Agent'" in result.data

    @pytest.mark.asyncio
    async def test_lookup_peers_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of peers."""
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["band_lookup_peers"]
        result = await lookup_peers(mock_context)

        # Pagination is hardcoded in the implementation (page=1, page_size=50)
        mock_tools.lookup_peers.assert_called_once_with(page=1, page_size=50)
        assert "Available agents" in result.data
        assert "Agent1" in result.data

    @pytest.mark.asyncio
    async def test_lookup_peers_handles_empty_result(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty peers list."""
        mock_tools.lookup_peers.return_value = {"data": [], "metadata": {}}
        set_session_tools(mock_context.session_id, mock_tools)

        lookup_peers = parlant_tools["band_lookup_peers"]
        result = await lookup_peers(mock_context)

        assert "No available agents found" in result.data

    @pytest.mark.asyncio
    async def test_get_participants_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of participants."""
        set_session_tools(mock_context.session_id, mock_tools)

        get_participants = parlant_tools["band_get_participants"]
        result = await get_participants(mock_context)

        mock_tools.get_participants.assert_called_once()
        assert "Current participants" in result.data
        assert "User1" in result.data

    @pytest.mark.asyncio
    async def test_get_participants_handles_empty_room(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty participants list."""
        mock_tools.get_participants.return_value = []
        set_session_tools(mock_context.session_id, mock_tools)

        get_participants = parlant_tools["band_get_participants"]
        result = await get_participants(mock_context)

        assert "No participants in the room" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_calls_tools(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.create_chatroom."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["band_create_chatroom"]
        result = await create_chatroom(mock_context, "task-456")

        mock_tools.create_chatroom.assert_called_once_with("task-456")
        assert "Created new chat room: new-room-123" in result.data

    @pytest.mark.asyncio
    async def test_create_chatroom_handles_empty_task_id(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle empty task_id."""
        set_session_tools(mock_context.session_id, mock_tools)

        create_chatroom = parlant_tools["band_create_chatroom"]
        result = await create_chatroom(mock_context, "")

        mock_tools.create_chatroom.assert_called_once_with(None)
        assert "Created new chat room" in result.data
