"""Tests for Step 4 AgentTools contract changes."""

from __future__ import annotations

import pytest

from thenvoi.core.exceptions import ThenvoiToolError
from thenvoi.testing.fake_tools import FakeAgentTools


class TestFakeAgentToolsSeededData:
    def test_default_empty_peers(self) -> None:
        tools = FakeAgentTools()
        assert tools._peers == []

    def test_seeded_participants(self) -> None:
        participants = [{"id": "p1", "name": "Alice"}]
        tools = FakeAgentTools(participants=participants)
        assert tools._participants == participants

    @pytest.mark.asyncio
    async def test_seeded_peers_returned(self) -> None:
        peers = [{"id": "u1", "name": "Bob", "type": "user"}]
        tools = FakeAgentTools(peers=peers)
        result = await tools.lookup_peers()
        assert result["peers"] == peers
        assert result["metadata"]["total"] == 1

    @pytest.mark.asyncio
    async def test_seeded_contacts_returned(self) -> None:
        contacts = [{"id": "c1", "handle": "@alice", "name": "Alice"}]
        tools = FakeAgentTools(contacts=contacts)
        result = await tools.list_contacts()
        assert result["contacts"] == contacts
        assert result["metadata"]["total_count"] == 1

    @pytest.mark.asyncio
    async def test_seeded_participants_returned(self) -> None:
        participants = [{"id": "p1", "name": "Alice"}]
        tools = FakeAgentTools(participants=participants)
        result = await tools.get_participants()
        assert result == participants


class TestFakeAgentToolsAssertions:
    @pytest.mark.asyncio
    async def test_assert_message_sent_content(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("hello", mentions=["@alice"])
        tools.assert_message_sent(content="hello")

    @pytest.mark.asyncio
    async def test_assert_message_sent_count(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("a", mentions=["@x"])
        await tools.send_message("b", mentions=["@y"])
        tools.assert_message_sent(count=2)

    @pytest.mark.asyncio
    async def test_assert_message_sent_fails(self) -> None:
        tools = FakeAgentTools()
        with pytest.raises(AssertionError, match="No message"):
            tools.assert_message_sent(content="nonexistent")

    @pytest.mark.asyncio
    async def test_assert_event_sent(self) -> None:
        tools = FakeAgentTools()
        await tools.send_event("data", "tool_call")
        tools.assert_event_sent(message_type="tool_call")

    @pytest.mark.asyncio
    async def test_assert_no_messages_sent(self) -> None:
        tools = FakeAgentTools()
        tools.assert_no_messages_sent()

    @pytest.mark.asyncio
    async def test_assert_no_messages_sent_fails(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("hello", mentions=["@x"])
        with pytest.raises(AssertionError, match="Expected no messages"):
            tools.assert_no_messages_sent()


class TestThenvoiToolErrorImport:
    """Verify ThenvoiToolError is usable for the send_message contract."""

    def test_can_raise_and_catch(self) -> None:
        with pytest.raises(ThenvoiToolError, match="At least one mention"):
            raise ThenvoiToolError(
                "At least one mention is required. "
                "Available participants: ['@alice']. "
                "Please retry with mentions specifying who this message is for."
            )


class TestFakeAgentToolsIsHubRoom:
    """FakeAgentTools must mirror AgentTools.is_hub_room for test parity."""

    def test_default_is_not_hub_room(self) -> None:
        tools = FakeAgentTools()
        assert tools.is_hub_room is False

    def test_is_hub_room_when_room_matches(self) -> None:
        tools = FakeAgentTools(room_id="hub-1", hub_room_id="hub-1")
        assert tools.is_hub_room is True

    def test_is_not_hub_room_when_room_differs(self) -> None:
        tools = FakeAgentTools(room_id="other-room", hub_room_id="hub-1")
        assert tools.is_hub_room is False

    def test_is_not_hub_room_without_hub_room_id(self) -> None:
        tools = FakeAgentTools(room_id="room-1")
        assert tools.is_hub_room is False
