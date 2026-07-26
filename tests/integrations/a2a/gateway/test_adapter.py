"""Behavior tests for the Band-backed A2A gateway executor."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueueLegacy
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)

from band.core.types import PlatformMessage
from band.integrations.a2a.gateway import A2AGatewayAdapter, A2AGatewayAdapterConfig
from band.integrations.a2a.gateway.adapter import BandAgentExecutor
from band.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask
from band.testing import FakeAgentTools
from band_rest.core.api_error import ApiError
from tests.integrations.a2a.gateway.helpers import make_peer


def make_platform_message(
    content: str,
    room_id: str = "room-123",
    message_type: str = "text",
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="peer-456",
        sender_type="Agent",
        sender_name="Weather Agent",
        message_type=message_type,
        metadata={},
        created_at=datetime.now(),
    )


def make_request(content: str = "What is the weather?") -> RequestContext:
    message = Message(
        message_id=str(uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text=content)],
    )
    return RequestContext(None, request=SendMessageRequest(message=message))


def configure_room_creation(adapter: A2AGatewayAdapter) -> None:
    response = MagicMock()
    response.data.id = "room-123"
    adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(return_value=response)
    adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
    adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock()
    adapter._rest.agent_api_events.create_agent_chat_event = AsyncMock()


def make_pending(event_queue: EventQueueLegacy) -> PendingA2ATask:
    return PendingA2ATask(
        task=Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        event_queue=event_queue,
        peer_id="weather",
        done=asyncio.Event(),
    )


class TestGatewayConfiguration:
    def test_timeout_is_adapter_configuration(self) -> None:
        config = A2AGatewayAdapterConfig(response_timeout_s=12)
        adapter = A2AGatewayAdapter(config=config)

        assert adapter.config is config
        assert adapter.config.response_timeout_s == 12

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="response_timeout_s"):
            A2AGatewayAdapterConfig(response_timeout_s=0)


class TestGatewayStartup:
    @pytest.mark.asyncio
    async def test_discovers_peers_and_starts_server(self) -> None:
        adapter = A2AGatewayAdapter()
        response = MagicMock()
        response.data = [make_peer("weather", "Weather Agent")]
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=response
        )

        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as server_type:
            server = MagicMock()
            server.start = AsyncMock()
            server_type.return_value = server

            await adapter.on_started("Gateway", "A2A Gateway")

        assert adapter._peers["weather-agent"].id == "weather"
        server.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_peer_discovery_only_for_rate_limits(self) -> None:
        adapter = A2AGatewayAdapter()
        response = MagicMock()
        response.data = []
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            side_effect=[ApiError(status_code=429, headers={}, body=""), response]
        )

        with (
            patch("band.integrations.a2a.gateway.adapter.GatewayServer") as server_type,
            patch(
                "band.integrations.a2a.gateway.adapter.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            server_type.return_value.start = AsyncMock()
            await adapter.on_started("Gateway", "A2A Gateway")

        assert adapter._rest.agent_api_peers.list_agent_peers.await_count == 2
        sleep.assert_awaited_once()


class TestGatewayExecution:
    @pytest.mark.asyncio
    async def test_initial_task_snapshot_stays_working_if_reply_is_immediate(self) -> None:
        adapter = A2AGatewayAdapter()
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        tools = FakeAgentTools()

        async def send_message(**_kwargs: object) -> None:
            await adapter.on_message(
                make_platform_message("Sunny"),
                tools,
                GatewaySessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=send_message
        )
        queue = EventQueueLegacy()

        await BandAgentExecutor(adapter, "weather").execute(make_request(), queue)

        initial = await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        assert initial.status.state == TaskState.TASK_STATE_WORKING
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_posts_to_band_and_returns_terminal_response(self) -> None:
        adapter = A2AGatewayAdapter()
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        sent = asyncio.Event()

        async def send_message(**_kwargs: object) -> None:
            sent.set()

        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=send_message
        )
        queue = EventQueueLegacy()
        execution = asyncio.create_task(
            BandAgentExecutor(adapter, "weather").execute(make_request(), queue)
        )
        await asyncio.wait_for(sent.wait(), timeout=1)

        initial = await queue.dequeue_event()
        assert initial.status.state == TaskState.TASK_STATE_WORKING

        await adapter.on_message(
            make_platform_message("Sunny"),
            FakeAgentTools(),
            GatewaySessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        await asyncio.wait_for(execution, timeout=1)
        final = await queue.dequeue_event()

        assert final.status.state == TaskState.TASK_STATE_COMPLETED
        assert final.status.message.parts[0].text == "Sunny"
        assert adapter._pending_tasks == {}

    @pytest.mark.asyncio
    async def test_keeps_stream_open_for_non_final_updates(self) -> None:
        adapter = A2AGatewayAdapter(
            config=A2AGatewayAdapterConfig(response_timeout_s=1)
        )
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        queue = EventQueueLegacy()
        sent = asyncio.Event()

        async def send_message(**_kwargs: object) -> None:
            sent.set()

        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=send_message
        )
        execution = asyncio.create_task(
            BandAgentExecutor(adapter, "weather").execute(make_request(), queue)
        )
        await asyncio.wait_for(sent.wait(), timeout=1)
        await queue.dequeue_event()

        await adapter.on_message(
            make_platform_message("Checking", message_type="thought"),
            FakeAgentTools(),
            GatewaySessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        update = await queue.dequeue_event()
        assert update.status.state == TaskState.TASK_STATE_WORKING
        assert not execution.done()

        await adapter.on_message(
            make_platform_message("Sunny"),
            FakeAgentTools(),
            GatewaySessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        await asyncio.wait_for(execution, timeout=1)
        assert (
            await queue.dequeue_event()
        ).status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_timeout_returns_terminal_failure(self) -> None:
        adapter = A2AGatewayAdapter(
            config=A2AGatewayAdapterConfig(response_timeout_s=0.01)
        )
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        queue = EventQueueLegacy()

        await BandAgentExecutor(adapter, "weather").execute(make_request(), queue)

        await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert adapter._pending_tasks == {}

    @pytest.mark.asyncio
    async def test_room_cleanup_returns_terminal_failure(self) -> None:
        adapter = A2AGatewayAdapter()
        queue = EventQueueLegacy()
        pending = make_pending(queue)
        adapter._pending_tasks["room-123"] = pending

        await adapter.on_cleanup("room-123")

        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert pending.done.is_set()
        assert adapter._pending_tasks == {}


class TestGatewayRoomState:
    @pytest.fixture
    def adapter(self) -> A2AGatewayAdapter:
        adapter = A2AGatewayAdapter()
        adapter._peers = {
            "weather": make_peer("weather", "Weather Agent"),
            "data": make_peer("data", "Data Agent"),
        }
        response = MagicMock()
        response.data.id = "new-room"
        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=response
        )
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_context_reuses_room_and_adds_new_peer(
        self, adapter: A2AGatewayAdapter
    ) -> None:
        room, context = await adapter._get_or_create_room("ctx", "weather")
        same_room, same_context = await adapter._get_or_create_room(context, "data")

        assert (room, context) == ("new-room", "ctx")
        assert (same_room, same_context) == (room, context)
        assert adapter._room_participants[room] == {"weather", "data"}
        assert adapter._rest.agent_api_chats.create_agent_chat.await_count == 1
        assert (
            adapter._rest.agent_api_participants.add_agent_chat_participant.await_count
            == 2
        )

    def test_rehydrate_merges_without_overwriting_live_context(self) -> None:
        adapter = A2AGatewayAdapter()
        adapter._context_to_room["ctx"] = "live-room"

        adapter._rehydrate(
            GatewaySessionState(
                context_to_room={"ctx": "old-room", "new": "new-room"},
                room_participants={"new-room": {"weather"}},
            )
        )

        assert adapter._context_to_room == {
            "ctx": "live-room",
            "new": "new-room",
        }
        assert adapter._room_participants["new-room"] == {"weather"}


class TestGatewayTranslation:
    @pytest.mark.parametrize(
        ("message_type", "state"),
        [
            ("thought", TaskState.TASK_STATE_WORKING),
            ("text", TaskState.TASK_STATE_COMPLETED),
            ("error", TaskState.TASK_STATE_FAILED),
        ],
    )
    def test_translates_band_message_state(self, message_type: str, state: int) -> None:
        adapter = A2AGatewayAdapter()
        task = make_pending(EventQueueLegacy()).task

        event = adapter._translate_to_a2a(
            make_platform_message("response", message_type=message_type), task
        )

        assert event.status.state == state
        assert event.status.message.parts[0].text == "response"
