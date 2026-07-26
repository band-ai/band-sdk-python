"""ASGI-level tests for the official A2A gateway routes."""

from __future__ import annotations

from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCard,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from starlette.testclient import TestClient
from a2a.utils import new_task

from band.integrations.a2a.gateway.server import GatewayServer
from tests.integrations.a2a.gateway.fixtures import make_peer


class FakeExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def build_server() -> GatewayServer:
    peer = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
    return GatewayServer(
        peers={"weather-agent": peer},
        peers_by_uuid={peer.id: peer},
        gateway_url="http://localhost:10000",
        port=10000,
        executor_factory=lambda _slug: FakeExecutor(),
    )


def test_agent_card_is_served_by_upstream_handler() -> None:
    response = TestClient(build_server()._build_app()).get(
        "/agents/weather-agent/.well-known/agent.json"
    )

    assert response.status_code == 200
    card = AgentCard.model_validate(response.json())
    assert card.name == "Weather Agent"
    assert card.additional_interfaces is not None
    assert card.additional_interfaces[0].transport == "JSONRPC"
    assert card.additional_interfaces[0].url.endswith("/agents/weather-agent")


def test_peers_listing_remains_gateway_owned() -> None:
    response = TestClient(build_server()._build_app()).get("/peers")

    assert response.status_code == 200
    assert response.json()["peers"] == [
        {
            "slug": "weather-agent",
            "id": "uuid-weather",
            "name": "Weather Agent",
            "description": "Gets weather info",
        }
    ]


def test_unknown_peer_is_not_resolved_by_a2a_routes() -> None:
    response = TestClient(build_server()._build_app()).get(
        "/agents/missing/.well-known/agent.json"
    )
    assert response.status_code == 404


def test_jsonrpc_method_errors_are_upstream_owned() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent",
        json={"jsonrpc": "2.0", "id": str(uuid4()), "method": "missing", "params": {}},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_jsonrpc_send_runs_through_official_handler_and_executor() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent",
        json={
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": "message-1",
                    "parts": [{"kind": "text", "text": "Hello"}],
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "request-1"
    assert body["result"]["status"]["state"] == "completed"


def test_rest_stream_runs_through_upstream_adapter() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent/v1/message:stream",
        json={
            "message": {
                "role": "ROLE_USER",
                "messageId": "message-1",
                "content": [{"text": "Hello"}],
            }
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert '"task":' in response.text
    assert '"state": "TASK_STATE_COMPLETED"' in response.text
