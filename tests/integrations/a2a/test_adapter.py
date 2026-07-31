"""Behavior tests for the outbound A2A adapter."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from a2a.helpers import new_text_message
from a2a.types import (
    Artifact,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)

from band.core.types import PlatformMessage
from band.integrations.a2a import A2AAdapter, A2AAuth, A2ASessionState
from band.testing import FakeAgentTools


def make_platform_message(content: str = "Hello") -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id="room-123",
        content=content,
        sender_id="user-456",
        sender_type="User",
        sender_name="Test User",
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )


def make_task(
    state: int = TaskState.TASK_STATE_COMPLETED,
    *,
    status_message: str | None = None,
    artifact_text: str | None = None,
) -> Task:
    task = Task(
        id="task-123",
        context_id="ctx-123",
        status=TaskStatus(state=state),
    )
    if status_message:
        task.status.message.CopyFrom(new_text_message(status_message))
    if artifact_text:
        task.artifacts.append(
            Artifact(artifact_id="artifact-1", parts=[Part(text=artifact_text)])
        )
    return task


def task_event(task: Task) -> StreamResponse:
    return StreamResponse(task=task)


def status_event(task: Task) -> StreamResponse:
    return StreamResponse(
        status_update={
            "task_id": task.id,
            "context_id": task.context_id,
            "status": task.status,
        }
    )


def artifact_event(
    task: Task,
    text: str,
    *,
    append: bool,
    last_chunk: bool,
) -> StreamResponse:
    return StreamResponse(
        artifact_update={
            "task_id": task.id,
            "context_id": task.context_id,
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text=text)],
            ),
            "append": append,
            "last_chunk": last_chunk,
        }
    )


async def stream(*events: StreamResponse):
    for event in events:
        yield event


class TestA2AAuth:
    def test_to_headers_combines_authentication_methods(self) -> None:
        auth = A2AAuth(
            api_key="key",
            bearer_token="token",
            headers={"X-Custom": "value"},
        )

        assert auth.to_headers() == {
            "X-API-Key": "key",
            "Authorization": "Bearer token",
            "X-Custom": "value",
        }


class TestA2AAdapterStartup:
    @pytest.mark.asyncio
    async def test_creates_client_with_auth_headers(self) -> None:
        adapter = A2AAdapter(
            remote_url="http://localhost:10000",
            auth=A2AAuth(api_key="key"),
        )
        client = MagicMock()

        with patch("band.integrations.a2a.adapter.ClientFactory") as factory_type:
            factory = factory_type.return_value
            factory.create_from_url = AsyncMock(return_value=client)

            await adapter.on_started("Agent", "Description")

        assert adapter._client is client
        config = factory_type.call_args.args[0]
        assert config.streaming is True
        assert adapter._http_client is not None
        assert adapter._http_client.headers["X-API-Key"] == "key"
        assert config.httpx_client is adapter._http_client, (
            "the factory must receive the adapter's own client — this identity "
            "is what carries auth to card resolution and every A2A request"
        )

        client.close = AsyncMock()
        await adapter.cleanup_all()


class TestA2AAdapterMessageFlow:
    @pytest.fixture
    def adapter(self) -> A2AAdapter:
        return A2AAdapter(remote_url="http://localhost:10000")

    @pytest.mark.asyncio
    async def test_forwards_band_message_as_a2a_request(
        self, adapter: A2AAdapter
    ) -> None:
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(
            return_value=stream(
                task_event(make_task(TaskState.TASK_STATE_WORKING)),
                status_event(make_task()),
            )
        )
        tools = FakeAgentTools()

        await adapter.on_message(
            make_platform_message("What is the weather?"),
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        request = adapter._client.send_message.call_args.args[0]
        assert isinstance(request, SendMessageRequest)
        assert request.message.role == Role.ROLE_USER
        assert request.message.parts[0].text == "What is the weather?"

    @pytest.mark.asyncio
    async def test_completed_task_posts_artifact_response(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            task_event(make_task(artifact_text="Final response")),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        assert tools.messages_sent[-1]["content"] == "Final response"
        assert tools.events_sent[-1]["metadata"]["a2a_task_state"] == (
            "TASK_STATE_COMPLETED"
        )

    @pytest.mark.asyncio
    async def test_streamed_artifact_chunks_are_posted_as_one_response(
        self, adapter: A2AAdapter
    ) -> None:
        working = make_task(TaskState.TASK_STATE_WORKING)
        completed = make_task(TaskState.TASK_STATE_COMPLETED)
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(
            return_value=stream(
                task_event(working),
                artifact_event(
                    working,
                    "Part one. ",
                    append=False,
                    last_chunk=False,
                ),
                artifact_event(
                    working,
                    "Part two.",
                    append=True,
                    last_chunk=True,
                ),
                status_event(completed),
            )
        )
        tools = FakeAgentTools()

        await adapter.on_message(
            make_platform_message(),
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert tools.messages_sent[-1]["content"] == "Part one. \nPart two."

    @pytest.mark.asyncio
    async def test_status_update_is_applied_to_task_and_completes_flow(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()
        task = make_task(TaskState.TASK_STATE_WORKING)

        await adapter._handle_event(
            task_event(task), tools, "room-123", "user-456", "Test User"
        )
        task.status.CopyFrom(
            TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED,
                message=new_text_message("Sunny"),
            )
        )
        await adapter._handle_event(
            status_event(task), tools, "room-123", "user-456", "Test User"
        )

        assert tools.messages_sent[-1]["content"] == "Sunny"
        assert adapter._tasks == {}

    @pytest.mark.asyncio
    async def test_terminal_task_is_finalized_even_when_band_delivery_fails(
        self, adapter: A2AAdapter
    ) -> None:
        """A failed delivery must not leave the room pointing at a done task.

        Nothing retries the delivery, so retaining the task would only make
        every later turn address a completed task_id and lose the terminal
        task event that rehydration depends on.
        """
        tools = FakeAgentTools()
        tools.send_message = AsyncMock(side_effect=RuntimeError("Band unavailable"))
        task = make_task(artifact_text="Final response")

        with pytest.raises(RuntimeError, match="Band unavailable"):
            await adapter._handle_event(
                task_event(task), tools, "room-123", "user-456", "Test User"
            )

        assert tools.events_sent[-1]["metadata"]["a2a_task_state"] == (
            "TASK_STATE_COMPLETED"
        ), "terminal task event must still be persisted for rehydration"
        assert adapter._tasks == {}, "next turn must start a fresh task"
        assert adapter._task_cache == {}
        assert adapter._task_senders == {}

    @pytest.mark.asyncio
    async def test_input_required_is_forwarded_and_persisted(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            task_event(
                make_task(
                    TaskState.TASK_STATE_INPUT_REQUIRED,
                    status_message="Which city?",
                )
            ),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        assert tools.messages_sent[-1]["content"] == "Which city?"
        assert tools.events_sent[-1]["metadata"]["a2a_task_state"] == (
            "TASK_STATE_INPUT_REQUIRED"
        )

    @pytest.mark.asyncio
    async def test_remote_error_is_posted_as_error_event(
        self, adapter: A2AAdapter
    ) -> None:
        """A remote A2A outage must surface in the room, not crash the turn."""
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(
            side_effect=RuntimeError("remote down")
        )
        tools = FakeAgentTools()

        await adapter.on_message(
            make_platform_message(),
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert tools.events_sent[-1]["message_type"] == "error"
        assert "remote down" in tools.events_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_failed_task_is_posted_as_error_event(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            task_event(make_task(TaskState.TASK_STATE_FAILED, status_message="boom")),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        error_events = [
            event for event in tools.events_sent if event["message_type"] == "error"
        ]
        assert error_events, "a failed task must produce an error event"
        assert error_events[-1]["content"] == "boom"
        assert error_events[-1]["metadata"]["a2a_state"] == "TASK_STATE_FAILED"

    @pytest.mark.asyncio
    async def test_working_status_text_is_narrated_as_thought(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            task_event(
                make_task(
                    TaskState.TASK_STATE_WORKING,
                    status_message="Checking sources",
                )
            ),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        assert tools.events_sent[-1]["message_type"] == "thought"
        assert tools.events_sent[-1]["content"] == "Checking sources"

    @pytest.mark.asyncio
    async def test_second_turn_carries_the_stored_context(
        self, adapter: A2AAdapter
    ) -> None:
        """Conversation continuity is the point of the context mapping."""
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(
            return_value=stream(task_event(make_task(artifact_text="done")))
        )
        tools = FakeAgentTools()
        turn = dict(is_session_bootstrap=False, room_id="room-123")

        await adapter.on_message(
            make_platform_message("first"), tools, A2ASessionState(), None, None, **turn
        )
        adapter._client.send_message = MagicMock(return_value=stream())
        await adapter.on_message(
            make_platform_message("second"),
            tools,
            A2ASessionState(),
            None,
            None,
            **turn,
        )

        request = adapter._client.send_message.call_args.args[0]
        assert request.message.context_id == "ctx-123", (
            "the second turn must continue the room's A2A context"
        )
        assert request.message.task_id == "", (
            "a completed task must not be continued on the next turn"
        )

    @pytest.mark.asyncio
    async def test_direct_message_response_is_forwarded(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            StreamResponse(message=new_text_message("Hello")),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        assert tools.messages_sent[-1]["content"] == "Hello"


class TestA2AAdapterShutdown:
    @pytest.mark.asyncio
    async def test_cleanup_all_closes_owned_clients(self) -> None:
        """Agent.stop() reaches the adapter only via cleanup_all, so the
        owned httpx transport must be released there."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.close = AsyncMock()
        adapter._http_client = httpx.AsyncClient()
        http_client = adapter._http_client

        await adapter.cleanup_all()

        assert http_client.is_closed, "owned httpx client must be closed"
        assert adapter._client is None
        assert adapter._http_client is None


class TestA2AAdapterSession:
    @pytest.mark.asyncio
    async def test_bootstrap_history_restores_context_for_the_turn(self) -> None:
        """Rehydration is gated on the bootstrap flag and must feed the
        restored context into the very message that triggered it."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(return_value=stream())
        state = A2ASessionState(context_id="ctx-9")

        await adapter.on_message(
            make_platform_message(),
            FakeAgentTools(),
            state,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        request = adapter._client.send_message.call_args.args[0]
        assert request.message.context_id == "ctx-9", (
            "a rejoined room must continue its persisted A2A context"
        )

    @pytest.mark.asyncio
    async def test_history_is_ignored_off_bootstrap(self) -> None:
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.send_message = MagicMock(return_value=stream())
        state = A2ASessionState(context_id="ctx-9")

        await adapter.on_message(
            make_platform_message(),
            FakeAgentTools(),
            state,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        request = adapter._client.send_message.call_args.args[0]
        assert request.message.context_id == "", (
            "history must only be applied on session bootstrap"
        )

    @pytest.mark.asyncio
    async def test_legacy_terminal_state_value_is_not_resubscribed(self) -> None:
        """Rooms with pre-migration history hold 0.x state strings."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.subscribe = MagicMock()

        await adapter._rehydrate_from_history(
            "room-123",
            A2ASessionState(
                context_id="ctx-123",
                task_id="task-123",
                task_state="completed",
            ),
        )

        adapter._client.subscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_resubscribe_failure_does_not_break_bootstrap(self) -> None:
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.subscribe = MagicMock(side_effect=RuntimeError("gone"))

        await adapter._rehydrate_from_history(
            "room-123",
            A2ASessionState(
                context_id="ctx-123",
                task_id="task-123",
                task_state="TASK_STATE_WORKING",
            ),
        )

        assert adapter._contexts["room-123"] == "ctx-123", (
            "a dead task must not cost the room its restored context"
        )

    @pytest.mark.asyncio
    async def test_rehydrates_context_and_resubscribes_active_task(self) -> None:
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.subscribe = MagicMock(
            return_value=stream(task_event(make_task(TaskState.TASK_STATE_WORKING)))
        )

        await adapter._rehydrate_from_history(
            "room-123",
            A2ASessionState(
                context_id="ctx-123",
                task_id="task-123",
                task_state="TASK_STATE_WORKING",
            ),
        )

        assert adapter._contexts["room-123"] == "ctx-123"
        assert adapter._tasks["room-123"] == "task-123"

    @pytest.mark.asyncio
    async def test_cleanup_reclaims_tasks_cached_by_resubscribe(self) -> None:
        """A resubscribed task has no sender entry, but must not outlive its room."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.subscribe = MagicMock(
            return_value=stream(task_event(make_task(TaskState.TASK_STATE_WORKING)))
        )
        await adapter._rehydrate_from_history(
            "room-123",
            A2ASessionState(
                context_id="ctx-123",
                task_id="task-123",
                task_state="TASK_STATE_WORKING",
            ),
        )
        assert adapter._task_cache, "resubscribe should have cached the task"

        await adapter.on_cleanup("room-123")

        assert adapter._task_cache == {}, (
            "room cleanup must reclaim cache entries that never got a sender"
        )

    @pytest.mark.asyncio
    async def test_does_not_resubscribe_terminal_task(self) -> None:
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        adapter._client.subscribe = MagicMock()

        await adapter._rehydrate_from_history(
            "room-123",
            A2ASessionState(
                context_id="ctx-123",
                task_id="task-123",
                task_state="TASK_STATE_COMPLETED",
            ),
        )

        adapter._client.subscribe.assert_not_called()
