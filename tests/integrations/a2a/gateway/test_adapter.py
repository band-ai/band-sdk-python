"""Behavior tests for the Band-backed A2A gateway executor."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
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
from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.integrations.a2a.gateway import A2AGatewayAdapter, A2AGatewayAdapterConfig
from band.integrations.a2a.gateway.adapter import (
    BandAgentExecutor,
    GatewayRequest,
    _redact_credentials,
)
from band.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask
from band.testing import FakeAgentTools
from tests.integrations.a2a.gateway.helpers import make_peer, peers_page


def make_platform_message(
    content: str,
    room_id: str = "room-123",
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="peer-456",
        sender_type="Agent",
        sender_name="Weather Agent",
        message_type=message_type,
        metadata=metadata if metadata is not None else {},
        created_at=datetime.now(),
    )


def make_request(content: str = "What is the weather?") -> RequestContext:
    message = Message(
        message_id=str(uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text=content)],
    )
    return RequestContext(None, request=SendMessageRequest(message=message))


def room_creation_response(room_id: str) -> MagicMock:
    response = MagicMock()
    response.data.id = room_id
    return response


def configure_room_creation(
    adapter: A2AGatewayAdapter, *, room_id: str = "room-123"
) -> None:
    adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
        return_value=room_creation_response(room_id)
    )
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
    )


class TestGatewayConfiguration:
    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="response_timeout_s"):
            A2AGatewayAdapterConfig(response_timeout_s=0)

    def test_gateway_url_derives_from_port(self) -> None:
        """Passing only port must not leave agent cards on the default URL."""
        adapter = A2AGatewayAdapter(port=8080, rest_client=MagicMock())
        assert adapter.gateway_url == "http://localhost:8080"

    def test_explicit_gateway_url_wins(self) -> None:
        adapter = A2AGatewayAdapter(
            gateway_url="https://gw.example.com", port=8080, rest_client=MagicMock()
        )
        assert adapter.gateway_url == "https://gw.example.com"


class TestGatewayStartup:
    @pytest.mark.asyncio
    async def test_discovers_peers_and_starts_server(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=peers_page([make_peer("weather", "Weather Agent")])
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
        assert (
            adapter._rest.agent_api_peers.list_agent_peers.call_args.kwargs[
                "request_options"
            ]
            == DEFAULT_REQUEST_OPTIONS
        )


class TestGatewayExecution:
    @pytest.mark.asyncio
    async def test_initial_task_snapshot_stays_working_if_reply_is_immediate(
        self,
    ) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        tools = FakeAgentTools()

        async def send_message(**_kwargs: object) -> MagicMock:
            await adapter.on_message(
                make_platform_message("Sunny"),
                tools,
                GatewaySessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
            return MagicMock(data=MagicMock())

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
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        sent = asyncio.Event()

        async def send_message(**_kwargs: object) -> MagicMock:
            sent.set()
            return MagicMock(data=MagicMock())

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
    async def test_send_to_band_fails_fast_when_post_message_refuses_blank_content(
        self,
    ) -> None:
        """Mirrors ACP's handle_prompt: a refused send must not fall through
        to _await_response and hang for response_timeout_s waiting on a
        reply to a message that was never posted."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        peer = make_peer("weather", "Weather Agent")
        request = GatewayRequest(
            peer=peer,
            room_id="room-123",
            context_id="ctx-123",
            pending=make_pending(EventQueueLegacy()),
        )

        with patch(
            "band.integrations.a2a.gateway.adapter.post_message",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="blank"):
                await adapter._send_to_band(request, make_request())

    @pytest.mark.asyncio
    async def test_keeps_stream_open_for_non_final_updates(self) -> None:
        # Generous timeout: the test never needs it to fire, and a tight one
        # turns a loaded CI runner into a spurious FAILED terminal event.
        adapter = A2AGatewayAdapter(
            config=A2AGatewayAdapterConfig(response_timeout_s=30),
            rest_client=MagicMock(),
        )
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        queue = EventQueueLegacy()
        sent = asyncio.Event()

        async def send_message(**_kwargs: object) -> MagicMock:
            sent.set()
            return MagicMock(data=MagicMock())

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
    async def test_timeout_returns_terminal_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = A2AGatewayAdapter(
            config=A2AGatewayAdapterConfig(response_timeout_s=0.01),
            rest_client=MagicMock(),
        )
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        queue = EventQueueLegacy()

        with caplog.at_level("INFO", logger="band.integrations.a2a.gateway.adapter"):
            await BandAgentExecutor(adapter, "weather").execute(make_request(), queue)

        await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert terminal.metadata["failure"]["provider"] == "a2a-gateway"
        assert terminal.metadata["failure"]["code"] == "timeout"
        assert adapter._pending_tasks == {}
        assert not any(
            "A2A request completed" in record.message for record in caplog.records
        ), "a timed-out request must not be logged as completed"

    @pytest.mark.asyncio
    async def test_send_failure_publishes_terminal_failure(self) -> None:
        """A REST failure while posting to Band must not leave the remote
        A2A caller waiting on a stuck WORKING task."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=RuntimeError("Band unavailable")
        )
        queue = EventQueueLegacy()

        with pytest.raises(RuntimeError, match="Band unavailable"):
            await BandAgentExecutor(adapter, "weather").execute(make_request(), queue)

        initial = await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        assert initial.status.state == TaskState.TASK_STATE_WORKING
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert terminal.status.message.parts[0].text == "A2A request failed"
        assert "Band unavailable" not in terminal.status.message.parts[0].text
        assert adapter._pending_tasks == {}
        failure = terminal.metadata["failure"]
        assert failure["provider"] == "a2a-gateway"
        assert failure["code"] == "RuntimeError"
        assert "Band unavailable" in failure["message"]

    @pytest.mark.asyncio
    async def test_send_failure_redacts_secrets_from_reported_metadata(self) -> None:
        """The sanitized exception text reaches the A2A client's metadata --
        a leaked bearer token or API key must not."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        configure_room_creation(adapter)
        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=RuntimeError(
                "upstream rejected Bearer abc123.def456 (api_key=sk-live-secret)"
            )
        )
        queue = EventQueueLegacy()

        with pytest.raises(RuntimeError):
            await BandAgentExecutor(adapter, "weather").execute(make_request(), queue)

        await queue.dequeue_event()
        terminal = await queue.dequeue_event()
        message = terminal.metadata["failure"]["message"]
        assert "abc123.def456" not in message
        assert "sk-live-secret" not in message
        assert "Bearer [REDACTED]" in message
        assert "api_key=[REDACTED]" in message

    def test_redact_credentials_full_value_scheme_prefixed(self) -> None:
        """A scheme-prefixed credential value (a space between the key and
        the secret) must be redacted in full, not just up to that space."""
        redacted = _redact_credentials("Authorization: ApiKey sk-live-abcdef123456")
        assert "sk-live-abcdef123456" not in redacted
        assert redacted == "Authorization=[REDACTED]"

    @pytest.mark.asyncio
    async def test_establish_request_raises_when_peer_missing(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())

        with pytest.raises(ValueError, match="Peer not found"):
            await adapter._establish_request(
                "missing", make_request(), EventQueueLegacy()
            )

    @pytest.mark.asyncio
    async def test_fetch_all_peers_accumulates_across_pages(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        full_page = [make_peer(f"peer-{i}", f"Peer {i}") for i in range(100)]
        partial_page = [make_peer("peer-100", "Peer 100")]
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            side_effect=[peers_page(full_page), peers_page(partial_page)]
        )

        peers = await adapter._fetch_all_peers()

        assert len(peers) == 101
        assert adapter._rest.agent_api_peers.list_agent_peers.await_count == 2
        first_call, second_call = (
            adapter._rest.agent_api_peers.list_agent_peers.call_args_list
        )
        assert first_call.kwargs["page"] == 1
        assert second_call.kwargs["page"] == 2

    @pytest.mark.asyncio
    async def test_cleanup_all_stops_the_hosted_server(self) -> None:
        """Agent.stop() reaches the adapter only via cleanup_all, so the
        self-hosted HTTP server must be stopped there."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        server = MagicMock()
        server.stop = AsyncMock()
        adapter._server = server

        await adapter.cleanup_all()

        server.stop.assert_awaited_once()
        assert adapter._server is None

    @pytest.mark.asyncio
    async def test_cleanup_all_fails_inflight_requests(self) -> None:
        """A shutdown must not leave remote clients waiting out the full
        response timeout."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)
        adapter._pending_tasks["room-123"] = pending

        await adapter.cleanup_all()

        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert not terminal.metadata, "a gateway shutdown is not a provider failure"
        assert pending.done.is_set()
        assert adapter._pending_tasks == {}

    @pytest.mark.asyncio
    async def test_concurrent_request_for_room_is_rejected_without_id_leak(
        self,
    ) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._pending_tasks["room-123"] = make_pending(EventQueueLegacy())

        with pytest.raises(RuntimeError) as excinfo:
            async with adapter.pending_task(
                "room-123", make_pending(EventQueueLegacy())
            ):
                pass

        assert "room-123" not in str(excinfo.value), (
            "the error reaches the remote A2A client — internal room ids must not leak"
        )

    @pytest.mark.asyncio
    async def test_room_cleanup_returns_terminal_failure(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)
        adapter._pending_tasks["room-123"] = pending

        await adapter.on_cleanup("room-123")

        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_FAILED
        assert not terminal.metadata, "a room closing is not a provider failure"
        assert pending.done.is_set()
        assert adapter._pending_tasks == {}


class TestGatewayRoomState:
    @pytest.fixture
    def adapter(self) -> A2AGatewayAdapter:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        adapter._peers = {
            "weather": make_peer("weather", "Weather Agent"),
            "data": make_peer("data", "Data Agent"),
        }
        configure_room_creation(adapter, room_id="new-room")
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

    @pytest.mark.asyncio
    async def test_different_contexts_get_different_rooms(
        self, adapter: A2AGatewayAdapter
    ) -> None:
        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=[
                room_creation_response("room-a"),
                room_creation_response("room-b"),
            ]
        )

        room_a, _ = await adapter._get_or_create_room("ctx-a", "weather")
        room_b, _ = await adapter._get_or_create_room("ctx-b", "weather")

        assert (room_a, room_b) == ("room-a", "room-b"), (
            "distinct A2A contexts must not share a Band room"
        )

    @pytest.mark.asyncio
    async def test_participant_add_failure_leaves_no_partial_room_state(
        self, adapter: A2AGatewayAdapter
    ) -> None:
        """Regression coverage: a REST failure after room creation currently
        leaves no context/room mapping behind, so a retry creates a brand
        new room rather than reusing the one that was just orphaned."""
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock(
            side_effect=RuntimeError("participant add failed")
        )

        with pytest.raises(RuntimeError, match="participant add failed"):
            await adapter._get_or_create_room("ctx", "weather")

        assert adapter._context_to_room == {}
        assert adapter._room_participants == {}

    def test_rehydrate_merges_without_overwriting_live_context(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
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


class TestPendingTaskLifecycle:
    @pytest.mark.asyncio
    async def test_second_terminal_transition_is_a_no_op(self) -> None:
        """The timeout in _await_response races the real reply from
        on_message; whichever loses must not publish a second terminal."""
        queue = EventQueueLegacy()
        pending = make_pending(queue)

        await pending.complete_with_message("done")
        await pending.fail("late timeout")

        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.dequeue_event(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_progress_after_terminal_is_dropped(self) -> None:
        queue = EventQueueLegacy()
        pending = make_pending(queue)

        await pending.complete_with_message("done")
        await pending.report_progress("late narration")

        terminal = await queue.dequeue_event()
        assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.dequeue_event(), timeout=0.05)


class TestGatewayResponses:
    @pytest.mark.parametrize(
        ("message_type", "state"),
        [
            ("thought", TaskState.TASK_STATE_WORKING),
            ("text", TaskState.TASK_STATE_COMPLETED),
            ("error", TaskState.TASK_STATE_FAILED),
        ],
    )
    async def test_publishes_band_message_with_matching_task_state(
        self, message_type: str, state: int
    ) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)

        await adapter._publish_band_response(
            pending,
            make_platform_message("response", message_type=message_type),
        )
        event = await queue.dequeue_event()

        assert event.status.state == state
        assert event.status.message.parts[0].text == "response"

    @pytest.mark.asyncio
    async def test_relays_peers_own_agent_failure_unchanged(self) -> None:
        """The peer's adapter already built this AgentFailure (send_failure) --
        the gateway must relay it as-is, not re-tag its provider as
        "a2a-gateway"."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)
        peer_failure = {
            "provider": "codex",
            "code": "ContextWindowExceeded",
            "message": "context window exceeded",
            "detail": None,
        }

        await adapter._publish_band_response(
            pending,
            make_platform_message(
                "context window exceeded",
                message_type="error",
                metadata={"failure": peer_failure},
            ),
        )
        event = await queue.dequeue_event()

        assert event.status.state == TaskState.TASK_STATE_FAILED
        assert event.metadata["failure"]["provider"] == "codex"
        assert event.metadata["failure"]["code"] == "ContextWindowExceeded"

    @pytest.mark.asyncio
    async def test_relayed_peer_failure_redacts_embedded_credentials(self) -> None:
        """A peer's own AgentFailure can embed a raw provider exception
        message -- redact it the same as this gateway's own exception path
        before it reaches an external A2A client."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)
        secret_message = "upstream rejected token=sk-live-secret"
        peer_failure = {
            "provider": "codex",
            "code": "Unauthorized",
            "message": secret_message,
            "detail": None,
        }

        await adapter._publish_band_response(
            pending,
            make_platform_message(
                secret_message,
                message_type="error",
                metadata={"failure": peer_failure},
            ),
        )
        event = await queue.dequeue_event()

        assert event.status.state == TaskState.TASK_STATE_FAILED
        assert "sk-live-secret" not in event.metadata["failure"]["message"]
        assert "sk-live-secret" not in event.status.message.parts[0].text

    @pytest.mark.asyncio
    async def test_plain_error_message_without_failure_metadata_still_fails(
        self,
    ) -> None:
        """A peer that never migrated to send_failure still fails the task --
        it just carries no structured metadata."""
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        queue = EventQueueLegacy()
        pending = make_pending(queue)

        await adapter._publish_band_response(
            pending,
            make_platform_message("something broke", message_type="error"),
        )
        event = await queue.dequeue_event()

        assert event.status.state == TaskState.TASK_STATE_FAILED
        assert not event.metadata
