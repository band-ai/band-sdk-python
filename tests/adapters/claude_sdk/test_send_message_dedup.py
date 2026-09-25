from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.types import PlatformMessage
from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools


class TestSendMessageDedupWiring:
    """Tests that on_message wires the dedup shim correctly."""

    @pytest.mark.asyncio
    async def test_wraps_tools_by_default(self, sample_message, mock_tools):
        """By default, on_message stores a DedupingAgentTools wrapper."""

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        stored = adapter._room_tools["room-1"]
        assert isinstance(stored, DedupingAgentTools)
        assert stored._inner is mock_tools

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_wrapping(self, sample_message, mock_tools):
        """ttl=0 keeps the raw tools — no shim — for operators who opt out."""

        adapter = ClaudeSDKAdapter(send_message_dedup_ttl_seconds=0)
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        stored = adapter._room_tools["room-1"]
        assert stored is mock_tools
        assert not isinstance(stored, DedupingAgentTools)

    def test_negative_ttl_rejected(self):
        with pytest.raises(ValueError):
            ClaudeSDKAdapter(send_message_dedup_ttl_seconds=-1)

    @pytest.mark.asyncio
    async def test_duplicate_mcp_calls_collapse_via_room_tools(
        self, sample_message, mock_tools
    ):
        """End-to-end: two MCP-style calls through the stored wrapper hit
        the inner send_message exactly once."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        # Simulate the MCP backend resolving room_tools.get("room-1") on
        # each tool call (exactly what _create_mcp_backend wires up).
        stored = adapter._room_tools["room-1"]
        await stored.send_message("hello", ["alice"])
        await stored.send_message("hello", ["alice"])

        assert mock_tools.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_wrapper_persists_across_on_message_calls(self, sample_message):
        """Cross-turn regression: the dominant pattern is a duplicate
        tool call arriving *after* the original turn's Complete event. Since
        SimpleAdapter constructs a fresh AgentTools per inbound message, a
        wrapper rebuilt per on_message would drop the cache and let the
        post-Complete duplicate through.

        Drive two on_message calls with different inner tools (mirroring
        AgentTools.from_context being called per message), then fire two
        identical send_message calls against the stored wrapper — one before
        and one after the second on_message — and assert the duplicate is
        suppressed across the turn boundary.
        """

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools_turn1 = MagicMock()
        tools_turn1.send_message = AsyncMock(return_value={"id": "m1"})
        tools_turn2 = MagicMock()
        tools_turn2.send_message = AsyncMock(return_value={"id": "m2"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_turn1,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            wrapper_after_turn1 = adapter._room_tools["room-1"]
            assert isinstance(wrapper_after_turn1, DedupingAgentTools)

            # Original send during turn 1 (via the MCP-resolved wrapper).
            await wrapper_after_turn1.send_message("hi", ["alice"])
            assert tools_turn1.send_message.await_count == 1

            # Turn 2 arrives with a distinct platform message id;
            # SimpleAdapter builds a fresh AgentTools.
            turn2_message = PlatformMessage(
                id="msg-456",
                room_id=sample_message.room_id,
                content=sample_message.content,
                sender_id=sample_message.sender_id,
                sender_type=sample_message.sender_type,
                sender_name=sample_message.sender_name,
                message_type=sample_message.message_type,
                metadata=sample_message.metadata,
                created_at=sample_message.created_at,
            )
            await adapter.on_message(
                msg=turn2_message,
                tools=tools_turn2,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

            # SAME wrapper instance must still be stored — only _inner swapped.
            wrapper_after_turn2 = adapter._room_tools["room-1"]
            assert wrapper_after_turn2 is wrapper_after_turn1
            assert wrapper_after_turn2._inner is tools_turn2

            # The lingering duplicate from turn 1 fires now. It must hit
            # the cache and NOT POST through tools_turn2.
            await wrapper_after_turn2.send_message("hi", ["alice"])
            assert tools_turn2.send_message.await_count == 0
            # And tools_turn1 was not called again either.
            assert tools_turn1.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_on_cleanup_evicts_wrapper(self, sample_message, mock_tools):
        """on_cleanup must remove the wrapper so a re-entered room rebuilds
        fresh state (and so the cache cannot leak across detached sessions)."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.cleanup_session = AsyncMock()

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            assert "room-1" in adapter._room_tools

            await adapter.on_cleanup("room-1")
            assert "room-1" not in adapter._room_tools

    @pytest.mark.asyncio
    async def test_distinct_rooms_get_distinct_wrappers(self, sample_message):
        """Per-room isolation: identical ``(content, mentions)`` in two
        different rooms must POST twice — once per room — because rooms
        are independent conversations and the dedup window is a per-room
        guard, not a global one.

        Pins ``_room_tools`` keying behavior so a future refactor (e.g.
        a per-session or singleton tools cache) cannot silently turn the
        dedup wrapper into a tenant-wide message suppressor.
        """

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools_a = MagicMock()
        tools_a.send_message = AsyncMock(return_value={"id": "a"})
        tools_b = MagicMock()
        tools_b.send_message = AsyncMock(return_value={"id": "b"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_a,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-a",
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools_b,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-b",
            )

        wrapper_a = adapter._room_tools["room-a"]
        wrapper_b = adapter._room_tools["room-b"]
        assert isinstance(wrapper_a, DedupingAgentTools)
        assert isinstance(wrapper_b, DedupingAgentTools)
        assert wrapper_a is not wrapper_b

        # Identical sends in two distinct rooms must both reach their
        # respective inner tools — dedup is per-room, not per-tenant.
        await wrapper_a.send_message("hello", ["alice"])
        await wrapper_b.send_message("hello", ["alice"])
        assert tools_a.send_message.await_count == 1
        assert tools_b.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_update_inner_skipped_when_tools_identity_unchanged(
        self, sample_message
    ):
        """When the runtime hands the adapter the same tools object twice,
        ``update_inner`` is a no-op and must be skipped — otherwise we'd
        briefly contend on the wrapper's lock for no reason."""

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        tools = MagicMock()
        tools.send_message = AsyncMock(return_value={"id": "x"})

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            wrapper = adapter._room_tools["room-1"]
            assert isinstance(wrapper, DedupingAgentTools)

            with patch.object(
                wrapper, "update_inner", new_callable=AsyncMock
            ) as mock_update:
                await adapter.on_message(
                    msg=sample_message,
                    tools=tools,  # SAME instance
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-1",
                )
                mock_update.assert_not_awaited()
