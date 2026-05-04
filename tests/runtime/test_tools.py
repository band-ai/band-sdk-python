"""Tests for AgentTools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.runtime.tools import (
    TOOL_MODELS,
    AgentTools,
    SendMessageInput,
    SendEventInput,
    AddParticipantInput,
    LookupPeersInput,
    GetParticipantsInput,
    CreateChatroomInput,
    _matches_identifier,
)


@pytest.fixture
def mock_rest_client():
    """Mock AsyncRestClient for testing AgentTools."""
    client = MagicMock()

    # Mock create_agent_chat_message
    message_response = MagicMock()
    message_response.data = MagicMock()
    message_response.data.model_dump.return_value = {
        "id": "msg-123",
        "content": "Hello",
        "sender_id": "agent-1",
    }
    client.agent_api_messages.create_agent_chat_message = AsyncMock(
        return_value=message_response
    )

    # Mock create_agent_chat_event
    event_response = MagicMock()
    event_response.data = MagicMock()
    event_response.data.model_dump.return_value = {
        "id": "evt-123",
        "content": "Thinking...",
        "message_type": "thought",
    }
    client.agent_api_events.create_agent_chat_event = AsyncMock(
        return_value=event_response
    )

    # Mock list_agent_chat_participants
    participant1 = MagicMock()
    participant1.id = "user-1"
    participant1.name = "User One"
    participant1.type = "User"
    participant1.handle = "user-one"
    participant1.model_dump.return_value = {
        "id": "user-1",
        "name": "User One",
        "type": "User",
        "handle": "user-one",
    }
    client.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[participant1])
    )

    # Mock list_agent_peers
    peer1 = MagicMock()
    peer1.id = "agent-2"
    peer1.name = "Agent Two"
    peer1.type = "Agent"
    peer1.handle = "agent-two"
    peer1.description = "Another agent"
    peers_response = MagicMock()
    peers_response.data = [peer1]
    peers_response.metadata = MagicMock()
    peers_response.metadata.page = 1
    peers_response.metadata.page_size = 50
    peers_response.metadata.total_count = 1
    peers_response.metadata.total_pages = 1
    peers_response.model_dump = MagicMock(
        return_value={
            "data": [
                {
                    "id": "agent-2",
                    "name": "Agent Two",
                    "type": "Agent",
                    "description": "Another agent",
                }
            ],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 1,
                "total_pages": 1,
            },
        }
    )
    client.agent_api_peers.list_agent_peers = AsyncMock(return_value=peers_response)

    # Mock add_agent_chat_participant
    client.agent_api_participants.add_agent_chat_participant = AsyncMock()

    # Mock remove_agent_chat_participant
    client.agent_api_participants.remove_agent_chat_participant = AsyncMock()

    return client


@pytest.fixture
def participants():
    """Sample participants list."""
    return [
        {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        {"id": "user-2", "name": "User Two", "type": "User", "handle": "@user-two"},
    ]


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_list_memories_omits_none_filters(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_memories(page_size=25)

        mock_rest_client.agent_api_memories.list_agent_memories.assert_awaited_once_with(
            page_size=25,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_store_memory_omits_none_fields(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.store_memory(
            content="remember this",
            system="working",
            type="semantic",
            segment="user",
            thought="useful later",
        )

        call_kwargs = (
            mock_rest_client.agent_api_memories.create_agent_memory.call_args.kwargs
        )
        memory_payload = call_kwargs["memory"].model_dump(exclude_unset=True)
        assert "subject_id" not in memory_payload
        assert "metadata" not in memory_payload
        assert call_kwargs["request_options"] is DEFAULT_REQUEST_OPTIONS

    @pytest.mark.parametrize(
        ("tool_method", "rest_method"),
        [
            ("get_memory", "get_agent_memory"),
            ("supersede_memory", "supersede_agent_memory"),
            ("archive_memory", "archive_agent_memory"),
        ],
    )
    @pytest.mark.asyncio
    async def test_memory_mutation_calls_use_default_request_options(
        self,
        mock_rest_client,
        tool_method: str,
        rest_method: str,
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        rest_call = AsyncMock(return_value=response)
        setattr(mock_rest_client.agent_api_memories, rest_method, rest_call)
        tools = AgentTools("room-123", mock_rest_client)

        await getattr(tools, tool_method)("mem-123")

        rest_call.assert_awaited_once_with(
            id="mem-123",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )


class TestAgentToolsConstruction:
    """Test AgentTools initialization."""

    def test_init_stores_room_id(self, mock_rest_client):
        """Should store room_id."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.room_id == "room-123"

    def test_init_stores_rest_client(self, mock_rest_client):
        """Should store REST client."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.rest is mock_rest_client

    def test_init_empty_participants_by_default(self, mock_rest_client):
        """Should have empty participants by default."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools._participants == []

    def test_init_with_participants(self, mock_rest_client, participants):
        """Should accept participants."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        assert tools._participants == participants


class TestAgentToolsFromContext:
    """Test AgentTools.from_context() factory method."""

    def test_from_context_creates_tools(self, mock_rest_client, participants):
        """from_context() should create AgentTools from ExecutionContext."""
        # Mock ExecutionContext
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = participants

        tools = AgentTools.from_context(mock_ctx)

        assert tools.room_id == "room-456"
        assert tools.rest is mock_rest_client
        assert tools._participants == participants


class TestAgentToolsContextSyncBack:
    """Regression tests for AgentTools._ctx sync-back to ExecutionContext.

    Before the fix, AgentTools.from_context() copied ctx.participants (which
    returns a shallow copy via property).  Mutations to the tools instance
    (add/remove participant) never propagated back to the ExecutionContext.
    On the next turn, from_context() would copy the stale list again.
    """

    @pytest.mark.asyncio
    async def test_add_participant_syncs_back_to_ctx(self, mock_rest_client):
        """add_participant() must call ctx.add_participant() with full dict."""
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = []
        mock_ctx.hub_room_id = None
        mock_ctx.add_participant = MagicMock()

        # Override list_agent_chat_participants to return empty (avoid "already_in_room")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        tools = AgentTools.from_context(mock_ctx)
        assert tools._ctx is mock_ctx

        await tools.add_participant("Agent Two")

        mock_ctx.add_participant.assert_called_once()
        added = mock_ctx.add_participant.call_args.args[0]
        assert added["id"] == "agent-2"
        assert added["name"] == "Agent Two"
        assert added["type"] == "Agent"
        assert added["handle"] == "agent-two"

    @pytest.mark.asyncio
    async def test_remove_participant_syncs_back_to_ctx(self, mock_rest_client):
        """remove_participant() must call ctx.remove_participant() with correct ID."""
        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = [participant]
        mock_ctx.hub_room_id = None
        mock_ctx.remove_participant = MagicMock()

        # Return same participant from REST so snapshot matches
        p_mock = MagicMock()
        p_mock.id = "user-1"
        p_mock.name = "User One"
        p_mock.type = "User"
        p_mock.handle = "user-one"
        p_mock.model_dump.return_value = participant
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        tools = AgentTools.from_context(mock_ctx)
        await tools.remove_participant("User One")

        mock_ctx.remove_participant.assert_called_once_with("user-1")

    @pytest.mark.asyncio
    async def test_add_participant_persists_across_recreated_tools(
        self, mock_rest_client
    ):
        """Added participant must survive tools recreation via from_context().

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the mutation would be lost.
        """
        from thenvoi.runtime.execution import ExecutionContext

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )

        # Empty room
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        # Turn 1: add participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.add_participant("Agent Two")

        assert len(ctx._participants) == 1
        assert ctx._participants[0]["id"] == "agent-2"

        # Turn 2: recreate tools — participant must still be there
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 1
        assert tools2._participants[0]["id"] == "agent-2"

    @pytest.mark.asyncio
    async def test_remove_participant_persists_across_recreated_tools(
        self, mock_rest_client
    ):
        """Removed participant must stay removed after tools recreation.

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the removal would be lost.
        """
        from thenvoi.runtime.execution import ExecutionContext

        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )
        ctx._participants = [participant]

        # REST snapshot must match ctx._participants
        p_mock = MagicMock()
        p_mock.id = "user-1"
        p_mock.name = "User One"
        p_mock.type = "User"
        p_mock.handle = "user-one"
        p_mock.model_dump.return_value = participant
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        # Turn 1: remove participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.remove_participant("User One")

        assert len(ctx._participants) == 0

        # Turn 2: recreate tools — participant must stay removed
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 0


class TestAgentToolsSendMessage:
    """Test send_message tool."""

    async def test_send_message_success(self, mock_rest_client, participants):
        """send_message() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_send_message_resolves_mentions(self, mock_rest_client, participants):
        """send_message() should resolve mention names to IDs and handles."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello @User One!", mentions=["User One"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "@user-one"

    async def test_send_message_unknown_mention_raises(
        self, mock_rest_client, participants
    ):
        """send_message() should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant 'Unknown'"):
            await tools.send_message("Hello!", mentions=["Unknown"])

    async def test_send_message_no_response_raises(
        self, mock_rest_client, participants
    ):
        """send_message() should raise if no response data."""
        mock_rest_client.agent_api_messages.create_agent_chat_message.return_value = (
            MagicMock(data=None)
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(RuntimeError, match="Failed to send message"):
            await tools.send_message("Hello!", mentions=["User One"])


class TestAgentToolsSendEvent:
    """Test send_event tool."""

    async def test_send_event_success(self, mock_rest_client):
        """send_event() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.send_event("Thinking...", "thought")

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["message_type"] == "thought"
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()

    async def test_send_event_with_metadata(self, mock_rest_client):
        """send_event() should pass metadata."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.send_event("Error!", "error", metadata={"code": 500})

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        event = call_args.kwargs["event"]
        assert event.metadata == {"code": 500}

    async def test_send_event_no_response_raises(self, mock_rest_client):
        """send_event() should raise if no response data."""
        mock_rest_client.agent_api_events.create_agent_chat_event.return_value = (
            MagicMock(data=None)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to send event"):
            await tools.send_event("Error!", "error")


class TestMatchesIdentifier:
    """Tests for the _matches_identifier helper."""

    def test_match_by_handle(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "alice") is True

    def test_match_by_name(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "Alice Smith") is True

    def test_match_by_id(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "u-1") is True

    def test_case_insensitive(self):
        entity = {"handle": "Alice", "name": "ALICE SMITH", "id": "U-1"}
        assert _matches_identifier(entity, "alice") is True
        assert _matches_identifier(entity, "alice smith") is True
        assert _matches_identifier(entity, "u-1") is True

    def test_no_match(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "bob") is False

    def test_missing_fields(self):
        """Should handle entities with missing or None fields."""
        assert _matches_identifier({"name": "Alice"}, "Alice") is True
        assert _matches_identifier({"handle": None, "name": "Alice"}, "Alice") is True
        assert _matches_identifier({}, "anything") is False

    def test_at_prefix_normalization(self):
        """@alice and alice should match regardless of which side has the prefix."""
        entity_with_at = {"handle": "@alice", "name": "Alice Smith", "id": "u-1"}
        entity_without_at = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}

        # identifier has @, entity doesn't
        assert _matches_identifier(entity_without_at, "@alice") is True
        # entity has @, identifier doesn't
        assert _matches_identifier(entity_with_at, "alice") is True
        # both have @
        assert _matches_identifier(entity_with_at, "@alice") is True
        # neither has @
        assert _matches_identifier(entity_without_at, "alice") is True

    def test_empty_identifier(self):
        """Empty string should only match empty field values."""
        entity = {"handle": "alice", "name": "Alice", "id": "u-1"}
        assert _matches_identifier(entity, "") is False


class TestAgentToolsAddParticipant:
    """Test add_participant tool."""

    async def test_add_participant_by_name(self, mock_rest_client):
        """add_participant() should match by name and add via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("Agent Two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["role"] == "member"
        assert result["status"] == "added"
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_called_once()

    async def test_add_participant_by_handle(self, mock_rest_client):
        """add_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["status"] == "added"

    async def test_add_participant_by_id(self, mock_rest_client):
        """add_participant() should match by ID."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-2", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["status"] == "added"

    async def test_add_participant_already_in_room_by_handle(self, mock_rest_client):
        """add_participant() should detect already-in-room by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("user-one", role="member")

        assert result["id"] == "user-1"
        assert result["status"] == "already_in_room"
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_not_called()

    async def test_add_participant_ambiguous_name_resolved_by_handle(
        self, mock_rest_client
    ):
        """Two peers with the same display name — handle disambiguates (INT-287)."""
        peer_a = MagicMock()
        peer_a.id = "agent-a"
        peer_a.name = "Weather Agent"
        peer_a.type = "Agent"
        peer_a.handle = "@alice/weather"
        peer_a.description = "Alice's weather agent"

        peer_b = MagicMock()
        peer_b.id = "agent-b"
        peer_b.name = "Weather Agent"
        peer_b.type = "Agent"
        peer_b.handle = "@bob/weather"
        peer_b.description = "Bob's weather agent"

        peers_response = MagicMock()
        peers_response.data = [peer_a, peer_b]
        peers_response.metadata = MagicMock()
        peers_response.metadata.page = 1
        peers_response.metadata.page_size = 100
        peers_response.metadata.total_count = 2
        peers_response.metadata.total_pages = 1
        mock_rest_client.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=peers_response
        )

        tools = AgentTools("room-123", mock_rest_client)

        # Using handle should pick the correct one
        result = await tools.add_participant("@bob/weather", role="member")

        assert result["id"] == "agent-b"
        assert result["name"] == "Weather Agent"
        assert result["status"] == "added"

    async def test_add_participant_not_found_raises(self, mock_rest_client):
        """add_participant() should raise if peer not found."""
        # Return empty peers
        mock_rest_client.agent_api_peers.list_agent_peers.return_value = MagicMock(
            data=[], metadata=MagicMock(total_pages=1)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="Participant 'Unknown' not found"):
            await tools.add_participant("Unknown")


class TestAgentToolsRemoveParticipant:
    """Test remove_participant tool."""

    async def test_remove_participant_by_name(self, mock_rest_client):
        """remove_participant() should match by name and remove via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("User One")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"
        mock_rest_client.agent_api_participants.remove_agent_chat_participant.assert_called_once()

    async def test_remove_participant_by_handle(self, mock_rest_client):
        """remove_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("user-one")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"

    async def test_remove_participant_not_found_raises(self, mock_rest_client):
        """remove_participant() should raise if not in room."""
        # Return empty participants
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="not found in this room"):
            await tools.remove_participant("Unknown")


class TestAgentToolsLookupPeers:
    """Test lookup_peers tool."""

    async def test_lookup_peers_success(self, mock_rest_client):
        """lookup_peers() should return the Fern response directly."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.lookup_peers(page=1, page_size=50)

        # Now returns full Fern response with .data and .metadata
        assert len(result.data) == 1
        assert result.data[0].name == "Agent Two"
        assert result.metadata.page == 1

    async def test_lookup_peers_filters_by_room(self, mock_rest_client):
        """lookup_peers() should filter by not_in_chat."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.lookup_peers()

        call_args = mock_rest_client.agent_api_peers.list_agent_peers.call_args
        assert call_args.kwargs["not_in_chat"] == "room-123"


class TestAgentToolsGetParticipants:
    """Test get_participants tool."""

    async def test_get_participants_success(self, mock_rest_client):
        """get_participants() should return Fern participant models."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert len(result) == 1
        assert result[0].name == "User One"

    async def test_get_participants_empty(self, mock_rest_client):
        """get_participants() should return empty list if none."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert result == []

    async def test_get_participants_updates_cache(self, mock_rest_client):
        """get_participants() should refresh self._participants for mention resolution."""
        tools = AgentTools("room-123", mock_rest_client)
        assert tools._participants == []

        await tools.get_participants()

        assert tools._participants == [
            {
                "id": "user-1",
                "name": "User One",
                "type": "User",
                "handle": "user-one",
            }
        ]

    async def test_get_participants_preserves_cache_when_data_none(
        self, mock_rest_client
    ):
        """``data is None`` indicates a transient/unexpected response — the
        cache should be preserved rather than wiped (an agent should always
        be a participant in its own room)."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        cached = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
        ]
        tools = AgentTools("room-123", mock_rest_client, participants=cached)

        result = await tools.get_participants()

        assert result == []
        assert tools._participants == cached

    async def test_get_participants_clears_cache_on_empty_list(self, mock_rest_client):
        """An explicit empty list from the server is authoritative — cache should clear."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        stale = [{"id": "ghost", "name": "Ghost", "type": "User", "handle": "ghost"}]
        tools = AgentTools("room-123", mock_rest_client, participants=stale)

        await tools.get_participants()

        assert tools._participants == []

    async def test_get_participants_syncs_diff_to_ctx(self, mock_rest_client):
        """get_participants() should sync additions/removals to ExecutionContext
        so the refreshed cache survives turn boundaries."""
        old = {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}

        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-123"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = [old]
        mock_ctx.hub_room_id = None
        mock_ctx.add_participant = MagicMock()
        mock_ctx.remove_participant = MagicMock()

        # Server returns only a *new* participant — user-1 is gone, user-2 is new.
        new_p = MagicMock()
        new_p.id = "user-2"
        new_p.name = "User Two"
        new_p.type = "User"
        new_p.handle = "user-two"
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[new_p]))
        )

        tools = AgentTools.from_context(mock_ctx)
        await tools.get_participants()

        mock_ctx.remove_participant.assert_called_once_with("user-1")
        mock_ctx.add_participant.assert_called_once()
        added = mock_ctx.add_participant.call_args.args[0]
        assert added["id"] == "user-2"
        assert added["handle"] == "user-two"

    async def test_send_message_mentions_newly_discovered_participant(
        self, mock_rest_client
    ):
        """Mentioning a participant first seen via get_participants() should not raise."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.get_participants()
        await tools.send_message("Hi @user-one!", mentions=["user-one"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "user-one"


class TestAgentToolsCreateChatroom:
    """Test create_chatroom tool."""

    async def test_create_chatroom_success(self, mock_rest_client):
        """create_chatroom() should call REST API and return room ID."""
        mock_response = Mock()
        mock_response.data.id = "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom(task_id="task-789")

        assert result == "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat.assert_called_once()

    async def test_create_chatroom_without_task_id(self, mock_rest_client):
        """create_chatroom() should work without task_id."""
        mock_response = Mock()
        mock_response.data.id = "room-abc"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom()

        assert result == "room-abc"


class TestAgentToolsSchemas:
    """Test tool schema generation."""

    def test_tool_models_registry(self):
        """TOOL_MODELS should contain all tool input models."""
        assert "thenvoi_send_message" in TOOL_MODELS
        assert "thenvoi_send_event" in TOOL_MODELS
        assert "thenvoi_add_participant" in TOOL_MODELS
        assert "thenvoi_remove_participant" in TOOL_MODELS
        assert "thenvoi_lookup_peers" in TOOL_MODELS
        assert "thenvoi_get_participants" in TOOL_MODELS
        assert "thenvoi_create_chatroom" in TOOL_MODELS

    def test_tool_models_property(self, mock_rest_client):
        """tool_models property should return registry."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.tool_models is TOOL_MODELS

    def test_get_tool_schemas_openai(self, mock_rest_client):
        """get_tool_schemas('openai') should return OpenAI format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("openai")

        tool_names = [s["function"]["name"] for s in schemas]
        # Base platform tools
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_send_event" in tool_names
        assert "thenvoi_add_participant" in tool_names
        assert "thenvoi_remove_participant" in tool_names
        assert "thenvoi_get_participants" in tool_names
        assert "thenvoi_lookup_peers" in tool_names
        assert "thenvoi_create_chatroom" in tool_names
        # Contact tools included by default
        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_add_contact" in tool_names
        # Memory tools excluded by default
        assert "thenvoi_list_memories" not in tool_names
        assert "thenvoi_store_memory" not in tool_names

        send_msg = next(
            s for s in schemas if s["function"]["name"] == "thenvoi_send_message"
        )
        assert send_msg["type"] == "function"
        assert "parameters" in send_msg["function"]
        assert "description" in send_msg["function"]

    def test_get_tool_schemas_openai_with_memory(self, mock_rest_client):
        """get_tool_schemas('openai', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("openai", include_memory=True)

        tool_names = [s["function"]["name"] for s in schemas]
        # Memory tools present
        assert "thenvoi_list_memories" in tool_names
        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_get_memory" in tool_names
        assert "thenvoi_supersede_memory" in tool_names
        assert "thenvoi_archive_memory" in tool_names
        # Base and contact tools still present
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_list_contacts" in tool_names

    def test_get_tool_schemas_anthropic(self, mock_rest_client):
        """get_tool_schemas('anthropic') should return Anthropic format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("anthropic")

        tool_names = [s["name"] for s in schemas]
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_list_contacts" in tool_names
        assert "thenvoi_list_memories" not in tool_names

        send_msg = next(s for s in schemas if s["name"] == "thenvoi_send_message")
        assert "input_schema" in send_msg
        assert "description" in send_msg

    def test_get_tool_schemas_anthropic_with_memory(self, mock_rest_client):
        """get_tool_schemas('anthropic', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("anthropic", include_memory=True)

        tool_names = [s["name"] for s in schemas]
        assert "thenvoi_list_memories" in tool_names
        assert "thenvoi_store_memory" in tool_names
        assert "thenvoi_send_message" in tool_names
        assert "thenvoi_list_contacts" in tool_names


class TestAgentToolsExecuteToolCall:
    """Test execute_tool_call dispatch."""

    async def test_execute_send_message(self, mock_rest_client, participants):
        """execute_tool_call() should dispatch thenvoi_send_message."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "thenvoi_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert result["id"] == "msg-123"

    async def test_execute_send_event(self, mock_rest_client):
        """execute_tool_call() should dispatch thenvoi_send_event."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call(
            "thenvoi_send_event", {"content": "Thinking...", "message_type": "thought"}
        )

        assert result["message_type"] == "thought"

    async def test_execute_lookup_peers(self, mock_rest_client):
        """execute_tool_call() should dispatch thenvoi_lookup_peers."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("thenvoi_lookup_peers", {"page": 1})

        # execute_tool_call calls .model_dump() on the Fern response
        assert isinstance(result, dict)

    async def test_execute_get_participants(self, mock_rest_client):
        """execute_tool_call() should dispatch thenvoi_get_participants."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("thenvoi_get_participants", {})

        assert isinstance(result, list)
        assert result[0]["id"] == "user-1"
        assert result[0]["name"] == "User One"

    async def test_execute_unknown_tool(self, mock_rest_client):
        """execute_tool_call() should return error for unknown tool."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("unknown_tool", {})

        assert "Unknown tool" in result

    async def test_execute_validation_error(self, mock_rest_client):
        """execute_tool_call() should return validation error in LLM-friendly format."""
        tools = AgentTools("room-123", mock_rest_client)

        # Missing required field
        result = await tools.execute_tool_call(
            "thenvoi_send_message", {"content": "Hello"}
        )

        assert "Invalid arguments for thenvoi_send_message" in result
        assert "mentions" in result  # Should mention the missing field

    async def test_execute_runtime_error(self, mock_rest_client, participants):
        """execute_tool_call() should return execution error."""
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            Exception("Network error")
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "thenvoi_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert "Error executing" in result


class TestEmptyMentionsValidation:
    """Test that empty mentions return a helpful error with participant names."""

    async def test_raises_error_with_participant_names(
        self, mock_rest_client, participants
    ):
        """Should raise ThenvoiToolError listing available participants when mentions empty."""
        from thenvoi.core.exceptions import ThenvoiToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(
            ThenvoiToolError, match="At least one mention is required"
        ) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        assert "@user-one" in str(exc_info.value)
        assert "@user-two" in str(exc_info.value)
        # Should NOT have called the API
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_called()

    async def test_raises_error_when_mentions_none(
        self, mock_rest_client, participants
    ):
        """Should raise ThenvoiToolError when mentions is None."""
        from thenvoi.core.exceptions import ThenvoiToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ThenvoiToolError, match="At least one mention is required"):
            await tools.send_message("Hello!", mentions=None)

    async def test_uses_handle_when_available(self, mock_rest_client):
        """Should prefer handle over name in error message."""
        from thenvoi.core.exceptions import ThenvoiToolError

        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ThenvoiToolError, match="@user-one"):
            await tools.send_message("Hello!", mentions=[])

    async def test_uses_name_when_no_handle(self, mock_rest_client):
        """Should fall back to participant name when handle is missing."""
        from thenvoi.core.exceptions import ThenvoiToolError

        participants = [
            {"id": "user-1", "name": "User One", "type": "User"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ThenvoiToolError, match="User One"):
            await tools.send_message("Hello!", mentions=[])

    async def test_no_error_when_mentions_provided(
        self, mock_rest_client, participants
    ):
        """Should proceed normally when mentions are provided."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model; verify it has the expected attribute
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_execute_tool_call_raises_thenvoi_tool_error(
        self, mock_rest_client, participants
    ):
        """execute_tool_call lets ThenvoiToolError propagate for wrapper translation."""
        from thenvoi.core.exceptions import ThenvoiToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ThenvoiToolError, match="At least one mention is required"):
            await tools.execute_tool_call(
                "thenvoi_send_message", {"content": "Hello!", "mentions": []}
            )


class TestMentionResolution:
    """Test mention resolution logic."""

    def test_resolve_string_mentions(self, mock_rest_client, participants):
        """Should resolve string mentions to dicts with id and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_dict_mentions_with_id(self, mock_rest_client, participants):
        """Should pass through dict mentions with ID and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"id": "custom-id", "handle": "@custom"}])

        assert resolved[0] == {"id": "custom-id", "handle": "@custom"}

    def test_resolve_dict_mentions_without_id(self, mock_rest_client, participants):
        """Should resolve dict mentions without ID by name lookup."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"name": "User One"}])

        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_unknown_raises(self, mock_rest_client, participants):
        """Should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant"):
            tools._resolve_mentions(["Unknown Person"])


class TestHandleMentionResolution:
    """Test handle-based mention resolution."""

    def test_resolve_by_handle(self, mock_rest_client, participants):
        """Should resolve mentions by handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one"])

        assert len(resolved) == 1
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_handle_takes_priority(self, mock_rest_client):
        """Should try handle lookup before name lookup."""
        # Participant with handle different from name
        participants = [
            {
                "id": "agent-1",
                "name": "Weather Agent",
                "type": "Agent",
                "handle": "@john/weather",
            },
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        # Resolve by handle
        resolved = tools._resolve_mentions(["@john/weather"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

        # Resolve by name still works
        resolved = tools._resolve_mentions(["Weather Agent"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

    def test_resolve_mixed_handles_and_names(self, mock_rest_client, participants):
        """Should resolve a mix of handles and names."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_unknown_handle_raises(self, mock_rest_client, participants):
        """Should raise for unknown handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        # @ prefix is stripped during normalization
        with pytest.raises(ValueError, match="Unknown participant 'unknown'"):
            tools._resolve_mentions(["@unknown"])

    def test_resolve_participant_without_handle(self, mock_rest_client):
        """Should resolve by name when participant has no handle."""
        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": None},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One"])

        assert resolved[0] == {"id": "user-1", "handle": None}


class TestToolInputModels:
    """Test Pydantic tool input models."""

    def test_send_message_input_validation(self):
        """SendMessageInput should validate fields."""
        model = SendMessageInput(content="Hello", mentions=["User"])
        assert model.content == "Hello"
        assert model.mentions == ["User"]

    def test_send_message_input_accepts_empty_mentions(self):
        """SendMessageInput allows empty mentions (runtime validates instead)."""
        model = SendMessageInput(content="Hello", mentions=[])
        assert model.mentions == []

    def test_send_event_input_validation(self):
        """SendEventInput should validate fields."""
        model = SendEventInput(content="Thinking", message_type="thought")
        assert model.message_type == "thought"

    def test_send_event_input_validates_type(self):
        """SendEventInput should validate message_type."""
        with pytest.raises(Exception):
            SendEventInput(content="Test", message_type="invalid")

    def test_add_participant_input_defaults(self):
        """AddParticipantInput should have default role."""
        model = AddParticipantInput(identifier="User")
        assert model.role == "member"

    def test_add_participant_input_accepts_legacy_name_field(self):
        """AddParticipantInput should accept 'name' as alias for backward compat."""
        model = AddParticipantInput.model_validate({"name": "Agent Two"})
        assert model.identifier == "Agent Two"

    def test_remove_participant_input_accepts_legacy_name_field(self):
        """RemoveParticipantInput should accept 'name' as alias for backward compat."""
        from thenvoi.runtime.tools import RemoveParticipantInput

        model = RemoveParticipantInput.model_validate({"name": "User One"})
        assert model.identifier == "User One"

    def test_lookup_peers_input_defaults(self):
        """LookupPeersInput should have defaults."""
        model = LookupPeersInput()
        assert model.page == 1
        assert model.page_size == 50

    def test_get_participants_input_no_fields(self):
        """GetParticipantsInput should have no required fields."""
        model = GetParticipantsInput()
        assert model is not None

    def test_create_chatroom_input_validation(self):
        """CreateChatroomInput should allow optional task_id."""
        model = CreateChatroomInput(task_id="task-123")
        assert model.task_id == "task-123"

    def test_create_chatroom_input_no_task_id(self):
        """CreateChatroomInput should work without task_id."""
        model = CreateChatroomInput()
        assert model.task_id is None
