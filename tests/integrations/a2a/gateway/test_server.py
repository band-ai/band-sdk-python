"""ASGI-level tests for the official A2A gateway routes."""

from __future__ import annotations

from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from starlette.testclient import TestClient

from band.integrations.a2a.gateway.server import GatewayServer
from band.integrations.a2a.protocol import new_task
from tests.integrations.a2a.gateway.helpers import make_peer


class FakeExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(context.message)
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


def build_server() -> GatewayServer:
    peer = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
    return GatewayServer(
        peers={"weather-agent": peer},
        peers_by_uuid={peer.id: peer},
        gateway_url="http://localhost:10000",
        port=10000,
        executor_factory=lambda _slug: FakeExecutor(),
    )


def test_agent_card_is_served_at_standard_and_legacy_paths() -> None:
    client = TestClient(build_server()._build_app())

    for path in (
        "/agents/weather-agent/.well-known/agent-card.json",
        "/agents/weather-agent/.well-known/agent.json",
    ):
        response = client.get(path)
        assert response.status_code == 200
        card = response.json()
        assert card["name"] == "Weather Agent"
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert card["supportedInterfaces"][0]["url"].endswith("/agents/weather-agent")


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
        "/agents/missing/.well-known/agent-card.json"
    )
    assert response.status_code == 404


def test_uuid_peer_alias_serves_the_same_agent_card() -> None:
    response = TestClient(build_server()._build_app()).get(
        "/agents/uuid-weather/.well-known/agent-card.json"
    )
    assert response.status_code == 200


def test_jsonrpc_method_errors_are_upstream_owned() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={"jsonrpc": "2.0", "id": str(uuid4()), "method": "missing", "params": {}},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_jsonrpc_send_runs_through_official_handler_and_executor() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "messageId": "message-1",
                    "parts": [{"text": "Hello"}],
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "request-1"
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_rest_stream_runs_through_upstream_handler() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent/message:stream",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "message-1",
                "role": "ROLE_USER",
                "parts": [{"text": "Hello"}],
            }
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert '"task":' in response.text
    assert '"state": "TASK_STATE_COMPLETED"' in response.text


def test_v03_jsonrpc_stream_accepts_legacy_payload() -> None:
    response = TestClient(build_server()._build_app()).post(
        "/agents/weather-agent",
        json={
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "message/stream",
            "params": {
                "message": {
                    "messageId": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}],
                },
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
