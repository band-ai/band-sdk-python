"""Local A2A protocol servers and executors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent

from band.integrations.a2a.gateway.server import GatewayServer
from tests.integrations.a2a.gateway.helpers import make_peer


class FakeExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ValueError("A2A request is missing its message")
            task = new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def build_server(
    *,
    port: int = 10000,
    executor_factory: Callable[[str], AgentExecutor] | None = None,
) -> GatewayServer:
    peer = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
    return GatewayServer(
        peers={"weather-agent": peer},
        gateway_url=f"http://localhost:{port}",
        port=port,
        executor_factory=executor_factory or (lambda _slug: FakeExecutor()),
    )


def build_multi_peer_server() -> GatewayServer:
    weather = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
    billing = make_peer("uuid-billing", "Billing Agent", "Answers billing questions")
    return GatewayServer(
        peers={"weather-agent": weather, "billing-agent": billing},
        gateway_url="http://localhost:10000",
        port=10000,
        executor_factory=lambda _slug: FakeExecutor(),
    )


def hello_message_body(message_id: str = "message-1") -> dict[str, object]:
    """The REST message:stream request body used by these tests."""
    return {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
        }
    }


def send_message_rpc(
    request_id: str, message_id: str = "message-1"
) -> dict[str, object]:
    """The JSON-RPC SendMessage request body used by these tests."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": message_id,
                "parts": [{"text": "Hello"}],
            }
        },
    }


class DelayedTwoStepExecutor(AgentExecutor):
    """Task, then a working update, then (after a pause) completion -- three
    distinct SSE events, so a stream cut short is distinguishable from a
    healthy one."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request is missing its message")
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await asyncio.sleep(0.3)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


class NeverFinishingExecutor(AgentExecutor):
    """Enqueues one event, then never returns -- holding the SSE response
    open indefinitely, the way a real long-running agent task would."""

    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ValueError("A2A request is missing its message")
            task = new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        try:
            await asyncio.Event().wait()
        finally:
            self.finished.set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError
