"""Behavior tests for the outbound A2A adapter."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
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
from band.integrations.a2a.protocol import text_message
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
        task.status.message.CopyFrom(text_message(status_message))
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
                message=text_message("Sunny"),
            )
        )
        await adapter._handle_event(
            status_event(task), tools, "room-123", "user-456", "Test User"
        )

        assert tools.messages_sent[-1]["content"] == "Sunny"
        assert adapter._tasks == {}

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
    async def test_direct_message_response_is_forwarded(
        self, adapter: A2AAdapter
    ) -> None:
        tools = FakeAgentTools()

        await adapter._handle_event(
            StreamResponse(message=text_message("Hello")),
            tools,
            "room-123",
            "user-456",
            "Test User",
        )

        assert tools.messages_sent[-1]["content"] == "Hello"


class TestA2AAdapterSession:
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
