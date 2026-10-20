"""Gateway binding behavior."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from a2a.utils.constants import PROTOCOL_VERSION_0_3

from tests.integrations.a2a.gateway.serverkit import (
    hello_message_body,
    send_message_rpc,
)


class TestProtocolBinding:
    async def test_agent_cards_use_the_schema_expected_by_each_protocol_version(
        self,
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

        legacy = await gateway_client.get(
            "/agents/weather-agent/.well-known/agent.json"
        )
        assert legacy.status_code == 200
        legacy_card = legacy.json()
        assert legacy_card["name"] == "Weather Agent"
        assert legacy_card["protocolVersion"] == PROTOCOL_VERSION_0_3
        assert legacy_card["url"].endswith("/agents/weather-agent")
        assert "supportedInterfaces" not in legacy_card

    async def test_peers_listing_remains_gateway_owned(
        self,
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
        self,
        gateway_client: httpx.AsyncClient,
    ) -> None:
        response = await gateway_client.get(
            "/agents/missing/.well-known/agent-card.json"
        )
        assert response.status_code == 404

    async def test_uuid_peer_alias_serves_the_same_agent_card(
        self,
        gateway_client: httpx.AsyncClient,
    ) -> None:
        response = await gateway_client.get(
            "/agents/uuid-weather/.well-known/agent-card.json"
        )
        assert response.status_code == 200

    async def test_jsonrpc_method_errors_are_upstream_owned(
        self,
        gateway_client: httpx.AsyncClient,
    ) -> None:
        response = await gateway_client.post(
            "/agents/weather-agent",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "missing",
                "params": {},
            },
        )

        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32601

    async def test_malformed_json_body_falls_through_to_upstream_dispatch(
        self,
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
        self,
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
        self,
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
        self, gateway_client: httpx.AsyncClient, method: str
    ) -> None:
        response = await gateway_client.post(
            "/agents/weather-agent",
            headers={"A2A-Version": "1.0"},
            json={"jsonrpc": "2.0", "id": "request-1", "method": method, "params": {}},
        )

        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32602

    async def test_jsonrpc_send_runs_through_official_handler_and_executor(
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
