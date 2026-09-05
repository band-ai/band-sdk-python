"""ASGI-level tests for the official A2A gateway routes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_task_from_user_message
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.constants import PROTOCOL_VERSION_0_3
from httpx import ASGITransport

from band.integrations.a2a.gateway.server import SERVER_STOP_TIMEOUT_S, GatewayServer
from tests.integrations.a2a.gateway.helpers import make_peer
from tests.lifecycle import elapsed, held_open, running


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


@pytest_asyncio.fixture
async def gateway_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=build_server()._build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def multi_peer_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=build_multi_peer_server()._build_app())
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


async def test_malformed_json_body_falls_through_to_upstream_dispatch(
    gateway_client: httpx.AsyncClient,
) -> None:
    """A body ``request.json()`` can't parse degrades ``method`` to ``None``,
    which skips the closed-method guard entirely -- the upstream dispatcher
    owns reporting the parse error, same as any other non-blocked method."""
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0", "Content-Type": "application/json"},
        content=b"{not valid json",
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32700


async def test_non_scalar_request_id_is_normalized_to_null(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": [1, 2, 3],
            "method": "ListTasks",
            "params": {},
        },
    )

    body = response.json()
    assert body["id"] is None, "a non-str/int id must not be echoed back verbatim"
    assert body["error"]["code"] == -32601


async def test_send_streaming_message_runs_through_official_handler(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "SendStreamingMessage",
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
    assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.parametrize("method", ["GetTask", "CancelTask"])
async def test_task_methods_without_id_are_rejected_by_upstream_handler(
    gateway_client: httpx.AsyncClient, method: str
) -> None:
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json={"jsonrpc": "2.0", "id": "request-1", "method": method, "params": {}},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


async def test_jsonrpc_send_runs_through_official_handler_and_executor(
    gateway_client: httpx.AsyncClient,
) -> None:
    response = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json=send_message_rpc("request-1"),
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
        json=hello_message_body(),
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert '"task":' in response.text
    assert '"state": "TASK_STATE_COMPLETED"' in response.text


async def test_rest_binding_is_reachable_for_every_peer_and_alias(
    multi_peer_client: httpx.AsyncClient,
) -> None:
    """Every peer must serve the REST route the gateway docs advertise.

    A gateway hosting more than one peer is the normal case, and each peer is
    addressable by slug and by UUID, so all four routes have to answer.
    """
    aliases = ("weather-agent", "uuid-weather", "billing-agent", "uuid-billing")

    reached_handler = {}
    for alias in aliases:
        response = await multi_peer_client.post(
            f"/agents/{alias}/message:stream",
            headers={"A2A-Version": "1.0"},
            json=hello_message_body(),
        )
        reached_handler[alias] = response.status_code

    assert reached_handler == dict.fromkeys(aliases, 200), (
        "each alias must reach its own REST handler — a 404 means another "
        "peer's routes shadowed it"
    )


async def test_task_rest_routes_are_not_exposed(
    gateway_client: httpx.AsyncClient,
) -> None:
    """The gateway has no auth layer, so the task surface must stay closed.

    Task listing/read would disclose past conversation content to any
    unauthenticated caller.
    """
    await gateway_client.post(
        "/agents/weather-agent/message:stream",
        headers={"A2A-Version": "1.0"},
        json=hello_message_body(),
    )

    listing = await gateway_client.get(
        "/agents/weather-agent/tasks", headers={"A2A-Version": "1.0"}
    )
    assert listing.status_code == 404, (
        "unauthenticated task listing must not exist — it inlines room content"
    )


async def test_non_messaging_jsonrpc_methods_are_not_exposed(
    gateway_client: httpx.AsyncClient,
) -> None:
    """With no auth layer every caller shares one task-store identity, so
    enumeration and push-config methods must answer method-not-found."""
    closed_methods = (
        "ListTasks",
        "GetExtendedAgentCard",
        "tasks/pushNotificationConfig/list",
        "tasks/pushNotificationConfig/set",
    )
    for method in closed_methods:
        response = await gateway_client.post(
            "/agents/weather-agent",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": method,
                "params": {},
            },
        )
        assert response.json()["error"]["code"] == -32601, (
            f"{method} must stay closed — it would disclose or disrupt "
            "other callers' conversations"
        )


async def test_task_started_on_slug_is_visible_via_uuid_alias(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Aliases of one peer share a handler, so they share task state."""
    send = await gateway_client.post(
        "/agents/weather-agent",
        headers={"A2A-Version": "1.0"},
        json=send_message_rpc("request-1"),
    )
    task_id = send.json()["result"]["task"]["id"]

    fetched = await gateway_client.post(
        "/agents/uuid-weather",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": "request-2",
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )

    assert fetched.json()["result"]["id"] == task_id, (
        "a task created via the slug alias must be fetchable via the UUID alias"
    )


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


async def test_start_returns_only_once_the_server_is_listening() -> None:
    """A caller dialing in right after ``on_started()`` returns (e.g. a real
    A2A client, or one of the E2E smokes) must not race a socket that isn't
    accepting connections yet."""
    async with running(build_server(port=0)) as server:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{server.bound_port}/peers")
        assert response.status_code == 200


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


async def test_a_second_server_is_not_poisoned_by_a_prior_servers_shutdown() -> None:
    """Regression, reproducing the real failure directly rather than just
    the flag from test_disables_sse_starlette_automatic_graceful_drain: a
    second GatewayServer's live stream, opened only after a first one has
    stopped in the same process, must still deliver every event."""
    async with running(build_server(port=0)) as first:
        port1 = first.bound_port
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{port1}/agents/weather-agent/message:stream",
                headers={"A2A-Version": "1.0"},
                json=hello_message_body(),
            ) as response:
                async for _ in response.aiter_bytes():
                    pass

    second = GatewayServer(
        peers={"other-agent": make_peer("uuid-other", "Other Agent", "")},
        gateway_url="http://localhost:0",
        port=0,
        executor_factory=lambda _slug: DelayedTwoStepExecutor(),
    )
    async with running(second):
        port2 = second.bound_port
        events: list[str] = []
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"http://127.0.0.1:{port2}/agents/other-agent/message:stream",
                headers={"A2A-Version": "1.0"},
                json=hello_message_body(),
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events.append(line)

        assert len(events) >= 3, (
            f"got {len(events)} events, expected 3 (task, working, completed) -- "
            "the second server's stream was cut short by the first server's shutdown"
        )


class NeverFinishingExecutor(AgentExecutor):
    """Enqueues one event, then never returns -- holding the SSE response
    open indefinitely, the way a real long-running agent task would."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ValueError("A2A request is missing its message")
            task = new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        await asyncio.sleep(3600)  # never closes on its own

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


@pytest.mark.timeout(SERVER_STOP_TIMEOUT_S + 15.0)
async def test_stop_returns_promptly_with_a_still_open_message_stream() -> None:
    """Regression: gateway/server.py disables sse_starlette's cooperative
    shutdown drain, so a live message:stream connection has no other way to
    end on its own -- stop() must bound its wait via timeout_graceful_shutdown
    instead of hanging forever.

    Measures wall-clock time around a bare ``await server.stop()`` (no
    wrapping ``asyncio.wait_for``, which would cancel ``stop()`` from the
    outside and mask a real hang as a false pass) -- same rationale as
    LocalMCPServer's own equivalent regression test.
    """
    server = build_server(
        port=0, executor_factory=lambda _slug: NeverFinishingExecutor()
    )
    async with running(server):
        port = server.bound_port

        async def connect(ready: asyncio.Event) -> None:
            with suppress(Exception):
                # timeout=None: httpx's default 5s read timeout would otherwise
                # give up waiting for the next chunk and disconnect on its own
                # around the same mark as SERVER_STOP_TIMEOUT_S -- masking a
                # real server-side hang as a false pass, since the connection
                # would end for the wrong reason (a bored client) rather than
                # proving stop() itself is bounded.
                async with (
                    httpx.AsyncClient(timeout=None) as client,
                    client.stream(
                        "POST",
                        f"http://127.0.0.1:{port}/agents/weather-agent/message:stream",
                        headers={"A2A-Version": "1.0"},
                        json=hello_message_body(),
                    ) as response,
                ):
                    async for _ in response.aiter_bytes():
                        ready.set()

        async with held_open(connect):
            stop_elapsed = await elapsed(server.stop())

        assert stop_elapsed < SERVER_STOP_TIMEOUT_S + 5.0, (
            f"stop() took {stop_elapsed:.1f}s -- graceful shutdown is not "
            "bounded by SERVER_STOP_TIMEOUT_S"
        )
