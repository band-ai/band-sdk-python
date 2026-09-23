"""Tests for Step 4 AgentTools contract changes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.client.rest import ChatParticipant
from band.core.exceptions import BandToolError
from band.runtime.tools import serialize_tool_result
from band.testing.fake_tools import FakeAgentTools

ALICE_PARTICIPANT_SEED = {
    "id": "p1",
    "name": "Alice",
    "handle": "@alice",
    "role": "member",
    "status": "active",
    "type": "User",
}


class TestFakeAgentToolsSeededData:
    def test_default_empty_peers(self) -> None:
        tools = FakeAgentTools()
        assert tools._peers == []

    def test_seeded_participants_are_canonicalized_through_chat_participant(
        self,
    ) -> None:
        seed = ALICE_PARTICIPANT_SEED
        tools = FakeAgentTools(participants=[seed])
        assert tools._participants == [
            ChatParticipant.model_validate(seed).model_dump()
        ]

    def test_seeded_participants_reject_a_shape_missing_required_fields(self) -> None:
        """``role``/``status``/``type`` are required on the real Fern model --
        a seed missing them must fail loudly, not pass silently as it did
        before participants were validated at seed time."""
        with pytest.raises(ValidationError):
            FakeAgentTools(participants=[{"id": "p1", "name": "Alice"}])

    @pytest.mark.asyncio
    async def test_seeded_peers_returned(self) -> None:
        peers = [
            {
                "id": "u1",
                "name": "Bob",
                "type": "user",
                "handle": "@bob",
                "is_contact": False,
                "source": "internal",
                "online": True,
            }
        ]
        tools = FakeAgentTools(peers=peers)

        listing = serialize_tool_result(await tools.lookup_peers())

        assert [peer["name"] for peer in listing["data"]] == ["Bob"], (
            "Seeded peers must be served in the real SDK's data field"
        )
        assert listing["metadata"]["total_count"] == 1

    @pytest.mark.asyncio
    async def test_seeded_contacts_returned(self) -> None:
        contacts = [
            {
                "id": "c1",
                "handle": "@alice",
                "name": "Alice",
                "type": "User",
                "inserted_at": "2025-01-01T00:00:00Z",
                "online": True,
            }
        ]
        tools = FakeAgentTools(contacts=contacts)

        listing = serialize_tool_result(await tools.list_contacts())

        assert [contact["handle"] for contact in listing["data"]] == ["@alice"], (
            "Seeded contacts must be served in the real SDK's data field"
        )
        assert listing["metadata"]["total_count"] == 1

    @pytest.mark.asyncio
    async def test_seeded_participants_returned_as_chat_participant_models(
        self,
    ) -> None:
        seed = ALICE_PARTICIPANT_SEED
        tools = FakeAgentTools(participants=[seed])

        result = await tools.get_participants()

        assert result == [ChatParticipant.model_validate(seed)], (
            "get_participants must match AgentTools' real return type: a list "
            "of Fern ChatParticipant models, not raw seed dicts"
        )

    @pytest.mark.asyncio
    async def test_seeded_room_context_is_canonicalized_through_chat_message(
        self,
    ) -> None:
        seed = {
            "id": "msg-1",
            "content": "hi",
            "sender_id": "user-1",
            "sender_type": "User",
            "message_type": "text",
        }
        tools = FakeAgentTools(room_context=[seed])

        listing = await tools.fetch_room_context(room_id=tools.room_id)

        assert listing["data"][0]["content"] == "hi"
        assert listing["data"][0]["sender_id"] == "user-1"

    def test_seeded_room_context_rejects_a_shape_missing_required_fields(self) -> None:
        """``sender_id``/``sender_type``/``message_type`` are required on the
        real Fern ``ChatMessage`` -- a malformed seed must fail loudly."""
        with pytest.raises(ValidationError):
            FakeAgentTools(room_context=[{"id": "msg-1", "content": "hi"}])


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


class TestBandToolErrorImport:
    """Verify BandToolError is usable for the send_message contract."""

    def test_can_raise_and_catch(self) -> None:
        with pytest.raises(BandToolError, match="At least one mention"):
            raise BandToolError(
                "At least one mention is required. "
                "Available handles: ['@alice']. "
                "Use participant handles from the list."
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
