"""ASGI-level tests for the official A2A gateway routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest_asyncio
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_task_from_user_message
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.constants import PROTOCOL_VERSION_0_3
from httpx import ASGITransport

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


def build_server() -> GatewayServer:
    peer = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
    return GatewayServer(
        peers={"weather-agent": peer},
        peers_by_uuid={peer.id: peer},
        gateway_url="http://localhost:10000",
        port=10000,
        executor_factory=lambda _slug: FakeExecutor(),
    )


@pytest_asyncio.fixture
async def gateway_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=build_server()._build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_agent_cards_use_the_schema_expected_by_each_protocol_version(
    gateway_client: httpx.AsyncClient,
) -> None:
    standard = await gateway_client.get(
        "/agents/weather-agent/.well-known/agent-card.json"
    )
    assert standard.status_code == 200
    standard_card = standard.json()
    assert standard_card["name"] == "Weather Agent"
    assert standard_card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert standard_card["supportedInterfaces"][0]["url"].endswith(
        "/agents/weather-agent"
    )

    legacy = await gateway_client.get("/agents/weather-agent/.well-known/agent.json")
    assert legacy.status_code == 200
    legacy_card = legacy.json()
    assert legacy_card["name"] == "Weather Agent"
    assert legacy_card["protocolVersion"] == PROTOCOL_VERSION_0_3
    assert legacy_card["url"].endswith("/agents/weather-agent")
    assert "supportedInterfaces" not in legacy_card


async def test_peers_listing_remains_gateway_owned(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.get("/peers")

    assert response.status_code == 200
    assert response.json()["peers"] == [
        {
            "slug": "weather-agent",
            "id": "uuid-weather",
            "name": "Weather Agent",
            "description": "Gets weather info",
        }
    ]


async def test_unknown_peer_is_not_resolved_by_a2a_routes(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.get("/agents/missing/.well-known/agent-card.json")
    assert response.status_code == 404


async def test_uuid_peer_alias_serves_the_same_agent_card(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.get(
        "/agents/uuid-weather/.well-known/agent-card.json"
    )
    assert response.status_code == 200


async def test_jsonrpc_method_errors_are_upstream_owned(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={"jsonrpc": "2.0", "id": str(uuid4()), "method": "missing", "params": {}},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


async def test_jsonrpc_send_runs_through_official_handler_and_executor(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
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


async def test_rest_stream_runs_through_upstream_handler(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
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


async def test_v03_jsonrpc_stream_accepts_legacy_payload(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
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
