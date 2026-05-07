"""Tests for the kickoff / bootstrap_room_message feature.

These tests cover the synthetic-injection path that lets an agent start work
in a room from an initial message that did not come through the platform.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.client.streaming import MessageCreatedPayload, MessageMetadata
from thenvoi.platform.event import MessageEvent
from thenvoi.runtime.execution import ExecutionContext
from thenvoi.runtime.types import (
    PlatformMessage,
    SYNTHETIC_KICKOFF_SENDER_ID,
    SYNTHETIC_SENDER_TYPE,
)


@pytest.fixture
def mock_link():
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.subscribe_room = AsyncMock()
    return link


def _platform_message(
    *,
    msg_id: str = "kickoff:test-1",
    content: str = "begin work on the ticket",
    metadata: dict | None = None,
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id="room-1",
        content=content,
        sender_id=SYNTHETIC_KICKOFF_SENDER_ID,
        sender_type=SYNTHETIC_SENDER_TYPE,
        sender_name="Kickoff",
        message_type="message",
        metadata=metadata or {},
        created_at=datetime.now(timezone.utc),
    )


class TestExecutionBootstrapMessage:
    """Direct tests on ExecutionContext.bootstrap_message."""

    @pytest.mark.asyncio
    async def test_delivers_synthetic_event_to_handler(self, mock_link):
        handler = AsyncMock()
        ctx = ExecutionContext("room-1", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        try:
            await ctx.bootstrap_message(_platform_message(content="hello"))
            await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        finally:
            await ctx.stop()

        # Handler called with a MessageEvent carrying synthetic identity
        assert handler.await_count == 1
        delivered_event = handler.await_args.args[1]
        assert isinstance(delivered_event, MessageEvent)
        assert delivered_event.payload is not None
        assert delivered_event.payload.content == "hello"
        assert delivered_event.payload.sender_id == SYNTHETIC_KICKOFF_SENDER_ID
        assert delivered_event.payload.sender_type == SYNTHETIC_SENDER_TYPE

    @pytest.mark.asyncio
    async def test_no_platform_persistence_calls(self, mock_link):
        """Synthetic kickoff messages must skip mark_processing/mark_processed."""
        handler = AsyncMock()
        ctx = ExecutionContext("room-1", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        try:
            await ctx.bootstrap_message(_platform_message())
            await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        finally:
            await ctx.stop()

        mock_link.mark_processing.assert_not_called()
        mock_link.mark_processed.assert_not_called()
        mock_link.mark_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_preserves_caller_message_id(self, mock_link):
        """External systems depend on stable ids for retry/replay idempotency."""
        handler = AsyncMock()
        ctx = ExecutionContext("room-1", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        try:
            await ctx.bootstrap_message(_platform_message(msg_id="webhook:event-abc"))
            await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        finally:
            await ctx.stop()

        delivered_event = handler.await_args.args[1]
        assert delivered_event.payload.id == "webhook:event-abc"

    @pytest.mark.asyncio
    async def test_bootstrap_does_not_poison_sync_marker(self, mock_link):
        """A kickoff arriving before any real WS message must not become the sync point."""
        handler = AsyncMock()
        ctx = ExecutionContext("room-1", mock_link, handler, agent_id="agent-123")

        # Inject a kickoff first
        await ctx.bootstrap_message(_platform_message(msg_id="kickoff:should-not-mark"))
        assert ctx._first_ws_msg_id is None

        # Then a real WS message
        real_event = MessageEvent(
            room_id="room-1",
            payload=MessageCreatedPayload(
                id="real-msg-42",
                content="hi",
                sender_id="user-1",
                sender_type="User",
                sender_name="Alice",
                message_type="message",
                metadata=MessageMetadata(mentions=[], status="sent"),
                chat_room_id="room-1",
                inserted_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            ),
        )
        await ctx.on_event(real_event)
        assert ctx._first_ws_msg_id == "real-msg-42"


class TestRealSystemMessageStillTracked:
    """Regression: real platform 'System' messages must keep mark_* lifecycle."""

    @pytest.mark.asyncio
    async def test_real_system_message_calls_mark_processed(self, mock_link):
        handler = AsyncMock()
        ctx = ExecutionContext("room-1", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        try:
            real_system_event = MessageEvent(
                room_id="room-1",
                payload=MessageCreatedPayload(
                    id="real-sys-1",
                    content="moderator note",
                    sender_id="some-real-system-sender",  # NOT a synthetic id
                    sender_type=SYNTHETIC_SENDER_TYPE,
                    sender_name="System",
                    message_type="message",
                    metadata=MessageMetadata(mentions=[], status="sent"),
                    chat_room_id="room-1",
                    inserted_at="2024-01-01T00:00:00Z",
                    updated_at="2024-01-01T00:00:00Z",
                ),
            )
            await ctx.on_event(real_system_event)
            await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        finally:
            await ctx.stop()

        mock_link.mark_processing.assert_called_once_with("room-1", "real-sys-1")
        mock_link.mark_processed.assert_called_once_with("room-1", "real-sys-1")


class TestAgentRuntimeBootstrap:
    """AgentRuntime.bootstrap_room_message subscribes + ensures execution."""

    @pytest.mark.asyncio
    async def test_subscribes_when_room_unknown(self, mock_link):
        from thenvoi.runtime.runtime import AgentRuntime

        handler = AsyncMock()
        runtime = AgentRuntime(mock_link, "agent-123", on_execute=handler)

        await runtime.bootstrap_room_message("new-room", _platform_message())

        mock_link.subscribe_room.assert_awaited_once_with("new-room")
        assert "new-room" in runtime.presence.rooms
        assert "new-room" in runtime.executions

        # Cleanup
        for room_id in list(runtime.executions.keys()):
            await runtime.executions[room_id].stop()

    @pytest.mark.asyncio
    async def test_skips_subscribe_when_room_known(self, mock_link):
        from thenvoi.runtime.runtime import AgentRuntime

        handler = AsyncMock()
        runtime = AgentRuntime(mock_link, "agent-123", on_execute=handler)
        runtime.presence.rooms.add("known-room")

        await runtime.bootstrap_room_message("known-room", _platform_message())

        mock_link.subscribe_room.assert_not_called()

        for room_id in list(runtime.executions.keys()):
            await runtime.executions[room_id].stop()


class TestAgentKickoff:
    """Agent.kickoff and Agent.bootstrap_room_message public-entry tests."""

    @pytest.fixture
    def started_agent(self, mock_link):
        """Build a started Agent backed by a real AgentRuntime + mock link.

        Avoids reaching into private attributes — uses the public Agent
        constructor with a stubbed PlatformRuntime, then flips the started
        flag the same way Agent.start() would.
        """
        from thenvoi.agent import Agent
        from thenvoi.runtime.runtime import AgentRuntime

        handler = AsyncMock()
        agent_runtime = AgentRuntime(mock_link, "agent-123", on_execute=handler)

        # Build a PlatformRuntime stand-in that delegates bootstrap to the
        # real AgentRuntime so the full call chain is exercised.
        platform_runtime = MagicMock()
        platform_runtime.link = mock_link
        platform_runtime.bootstrap_room_message = AsyncMock(
            side_effect=agent_runtime.bootstrap_room_message
        )

        adapter = MagicMock()
        adapter.on_started = AsyncMock()
        adapter.on_event = AsyncMock()
        adapter.on_cleanup = AsyncMock()

        agent = Agent(runtime=platform_runtime, adapter=adapter)
        agent._started = True  # simulate post-start state without WS connect

        return agent, agent_runtime, handler

    @pytest.mark.asyncio
    async def test_kickoff_creates_room_when_not_provided(
        self, started_agent, mock_link
    ):
        agent, agent_runtime, handler = started_agent

        created = MagicMock()
        created.id = "fresh-room-9"
        mock_link.rest.agent_api_chats = MagicMock()
        mock_link.rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=MagicMock(data=created)
        )

        room_id = await agent.kickoff("go!", task_id="task-7")

        assert room_id == "fresh-room-9"
        mock_link.rest.agent_api_chats.create_agent_chat.assert_awaited_once()
        agent.runtime.bootstrap_room_message.assert_awaited_once()
        assert "fresh-room-9" in agent_runtime.executions

        # Confirm the synthetic message reached the adapter, not just that
        # the room was created and execution started.
        await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        delivered = handler.await_args.args[1]
        assert delivered.payload.content == "go!"
        assert delivered.payload.sender_id == SYNTHETIC_KICKOFF_SENDER_ID

        for r in list(agent_runtime.executions.keys()):
            await agent_runtime.executions[r].stop()

    @pytest.mark.asyncio
    async def test_kickoff_raises_specific_error_when_create_chat_returns_no_data(
        self, started_agent, mock_link
    ):
        agent, agent_runtime, _handler = started_agent
        mock_link.rest.agent_api_chats = MagicMock()
        mock_link.rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=MagicMock(data=None)
        )
        with pytest.raises(RuntimeError, match="task_id='task-9'"):
            await agent.kickoff("go!", task_id="task-9")

    @pytest.mark.asyncio
    async def test_bootstrap_room_message_preserves_caller_id_end_to_end(
        self, started_agent, mock_link
    ):
        """Public-entry test through Agent.bootstrap_room_message: a caller-
        supplied PlatformMessage id must reach the adapter unchanged."""
        agent, agent_runtime, handler = started_agent

        message = _platform_message(msg_id="webhook:event-xyz", content="run report")
        await agent.bootstrap_room_message("room-42", message)

        await asyncio.wait_for(_wait_for_handler(handler), timeout=2.0)
        delivered = handler.await_args.args[1]
        assert delivered.payload.id == "webhook:event-xyz"
        assert delivered.payload.content == "run report"

        for r in list(agent_runtime.executions.keys()):
            await agent_runtime.executions[r].stop()

    @pytest.mark.asyncio
    async def test_kickoff_raises_when_agent_not_started(self, mock_link):
        from thenvoi.agent import Agent

        adapter = MagicMock()
        platform_runtime = MagicMock()
        agent = Agent(runtime=platform_runtime, adapter=adapter)
        # _started defaults to False

        with pytest.raises(RuntimeError, match="Agent not started"):
            await agent.kickoff("nope")

    @pytest.mark.asyncio
    async def test_bootstrap_room_message_raises_when_agent_not_started(
        self, mock_link
    ):
        from thenvoi.agent import Agent

        adapter = MagicMock()
        platform_runtime = MagicMock()
        agent = Agent(runtime=platform_runtime, adapter=adapter)

        with pytest.raises(RuntimeError, match="Agent not started"):
            await agent.bootstrap_room_message("room-1", _platform_message())


class TestRoomAddedDedupe:
    """Regression: WS room_added for a kickoff-claimed room must not double-subscribe."""

    @pytest.mark.asyncio
    async def test_room_added_skips_subscribe_when_room_already_tracked(
        self, mock_link
    ):
        from thenvoi.client.streaming import RoomAddedPayload
        from thenvoi.platform.event import RoomAddedEvent
        from thenvoi.runtime.presence import RoomPresence

        presence = RoomPresence(mock_link)
        # Simulate kickoff having already claimed and subscribed to the room.
        presence.rooms.add("claimed-room")
        on_joined = AsyncMock()
        presence.on_room_joined = on_joined

        event = RoomAddedEvent(
            room_id="claimed-room",
            payload=RoomAddedPayload(
                id="claimed-room",
                inserted_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            ),
        )
        await presence._handle_room_added(event)

        # subscribe_room must not be called a second time, and on_room_joined
        # should not re-fire (execution would already exist anyway, but the
        # callback contract is "once per join").
        mock_link.subscribe_room.assert_not_called()
        on_joined.assert_not_called()


# --- helpers ---


async def _wait_for_handler(handler: AsyncMock) -> None:
    while handler.await_count == 0:
        await asyncio.sleep(0.01)
