"""Tests for GatewayServer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from a2a.types import (
    Message as A2AMessage,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from starlette.testclient import TestClient

from band.integrations.a2a.gateway.server import GatewayServer
from band_rest import Peer


def make_peer(peer_id: str, name: str, description: str = "") -> Peer:
    """Create a mock Peer object."""
    return Peer(
        id=peer_id,
        name=name,
        type="Agent",
        description=description,
        handle=f"test/{name.lower().replace(' ', '-')}",
        is_contact=False,
        source="registry",
    )


class TestGatewayServerInit:
    """Tests for GatewayServer initialization."""

    def test_init_stores_config(self) -> None:
        """Should store configuration."""
        peer = make_peer("uuid-weather", "Weather Agent")
        peers = {"weather-agent": peer}
        peers_by_uuid = {"uuid-weather": peer}
        on_request = AsyncMock()

        server = GatewayServer(
            peers=peers,
            peers_by_uuid=peers_by_uuid,
            gateway_url="http://localhost:9000",
            port=9000,
            on_request=on_request,
        )

        assert server.peers == peers
        assert server.peers_by_uuid == peers_by_uuid
        assert server.gateway_url == "http://localhost:9000"
        assert server.port == 9000
        assert server.on_request == on_request

    def test_init_defaults(self) -> None:
        """Should start with no app or server task."""
        server = GatewayServer(
            peers={},
            peers_by_uuid={},
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=AsyncMock(),
        )

        assert server._app is None
        assert server._server_task is None


class TestGatewayServerBuildApp:
    """Tests for GatewayServer._build_app()."""

    def test_build_app_creates_starlette_app(self) -> None:
        """Should create a Starlette application."""
        peer = make_peer("uuid-weather", "Weather Agent")
        server = GatewayServer(
            peers={"weather-agent": peer},
            peers_by_uuid={"uuid-weather": peer},
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=AsyncMock(),
        )

        app = server._build_app()

        from starlette.applications import Starlette

        assert isinstance(app, Starlette)

    def test_build_app_has_agent_card_route(self) -> None:
        """Should have agent card discovery route."""
        peer = make_peer("uuid-weather", "Weather Agent")
        server = GatewayServer(
            peers={"weather-agent": peer},
            peers_by_uuid={"uuid-weather": peer},
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=AsyncMock(),
        )

        app = server._build_app()

        # Check routes
        route_paths = [r.path for r in app.routes]
        assert "/agents/{peer_id}/.well-known/agent.json" in route_paths

    def test_build_app_has_message_stream_route(self) -> None:
        """Should have message streaming route."""
        peer = make_peer("uuid-weather", "Weather Agent")
        server = GatewayServer(
            peers={"weather-agent": peer},
            peers_by_uuid={"uuid-weather": peer},
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=AsyncMock(),
        )

        app = server._build_app()

        # Check routes
        route_paths = [r.path for r in app.routes]
        assert "/agents/{peer_id}/v1/message:stream" in route_paths


class TestGatewayServerAgentCard:
    """Tests for agent card endpoint."""

    @pytest.fixture
    def server_with_peers(self) -> GatewayServer:
        """Create server with test peers."""
        weather = make_peer("uuid-weather", "Weather Agent", "Gets weather info")
        servicenow = make_peer("uuid-servicenow", "ServiceNow Agent", "Creates tickets")
        peers = {
            "weather-agent": weather,
            "servicenow-agent": servicenow,
        }
        peers_by_uuid = {
            "uuid-weather": weather,
            "uuid-servicenow": servicenow,
        }
        return GatewayServer(
            peers=peers,
            peers_by_uuid=peers_by_uuid,
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=AsyncMock(),
        )

    def test_agent_card_returns_valid_json(
        self, server_with_peers: GatewayServer
    ) -> None:
        """Should return valid AgentCard JSON for known peer (by slug)."""
        app = server_with_peers._build_app()
        client = TestClient(app)

        response = client.get("/agents/weather-agent/.well-known/agent.json")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Weather Agent"
        assert data["description"] == "Gets weather info"
        assert data["url"] == "http://localhost:10000/agents/weather-agent"

    def test_agent_card_includes_capabilities(
        self, server_with_peers: GatewayServer
    ) -> None:
        """Should include streaming capability."""
        app = server_with_peers._build_app()
        client = TestClient(app)

        response = client.get("/agents/weather-agent/.well-known/agent.json")

        data = response.json()
        assert data["capabilities"]["streaming"] is True

    def test_agent_card_includes_skills(self, server_with_peers: GatewayServer) -> None:
        """Should include default skill."""
        app = server_with_peers._build_app()
        client = TestClient(app)

        response = client.get("/agents/weather-agent/.well-known/agent.json")

        data = response.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "Weather Agent"
        assert "band" in data["skills"][0]["tags"]

    def test_agent_card_returns_404_for_unknown_peer(
        self, server_with_peers: GatewayServer
    ) -> None:
        """Should return 404 for unknown peer."""
        app = server_with_peers._build_app()
        client = TestClient(app)

        response = client.get("/agents/unknown/.well-known/agent.json")

        assert response.status_code == 404
        assert response.json()["error"] == "Not found"

    def test_agent_card_resolves_by_uuid(
        self, server_with_peers: GatewayServer
    ) -> None:
        """Should resolve agent by UUID as fallback."""
        app = server_with_peers._build_app()
        client = TestClient(app)

        response = client.get("/agents/uuid-weather/.well-known/agent.json")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Weather Agent"
        # URL should use the slug, not the UUID
        assert data["url"] == "http://localhost:10000/agents/weather-agent"


class TestGatewayServerMessageStream:
    """Tests for message streaming endpoint."""

    @pytest.fixture
    def server_with_callback(self) -> tuple[GatewayServer, AsyncMock]:
        """Create server with mock callback."""
        callback = AsyncMock()
        peer = make_peer("uuid-weather", "Weather Agent")
        peers = {"weather-agent": peer}
        peers_by_uuid = {"uuid-weather": peer}
        server = GatewayServer(
            peers=peers,
            peers_by_uuid=peers_by_uuid,
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=callback,
        )
        return server, callback

    def test_message_stream_returns_404_for_unknown_peer(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return 404 for unknown peer."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/unknown/v1/message:stream",
            json={
                "role": "user",
                "messageId": "msg-123",
                "parts": [{"text": "Hello"}],
            },
        )

        assert response.status_code == 404

    def test_message_stream_calls_on_request(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should call on_request callback with slug."""
        server, _ = server_with_callback

        # Track calls manually
        calls: list[tuple[str, A2AMessage]] = []

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            calls.append((peer_id, message))
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            client.post(
                "/agents/weather-agent/v1/message:stream",
                json={
                    "role": "user",
                    "messageId": "msg-123",
                    "parts": [{"kind": "text", "text": "What is the weather?"}],
                },
            )

        # Callback should have been called with slug
        assert len(calls) == 1
        assert calls[0][0] == "weather-agent"  # slug
        assert isinstance(calls[0][1], A2AMessage)  # message

    def test_message_stream_returns_sse_content_type(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return SSE content type."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/weather-agent/v1/message:stream",
                json={
                    "role": "user",
                    "messageId": "msg-123",
                    "parts": [{"kind": "text", "text": "Hello"}],
                },
            )

        assert "text/event-stream" in response.headers["content-type"]

    def test_message_stream_yields_events_as_json(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should yield events as SSE-formatted JSON."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/weather-agent/v1/message:stream",
                json={
                    "role": "user",
                    "messageId": "msg-123",
                    "parts": [{"kind": "text", "text": "Hello"}],
                },
            )

        # Response should be SSE formatted
        content = response.text
        assert content.startswith("data: ")
        assert '"taskId":"task-123"' in content
        assert '"final":true' in content

    def test_message_stream_returns_400_for_invalid_payload(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Legacy stream endpoint should reject malformed message payloads."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/weather-agent/v1/message:stream",
            json={"jsonrpc": "2.0", "method": "message/send"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Invalid A2A message payload"
        assert data["details"]

    def test_message_stream_handles_multiple_events(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should yield multiple events."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.working),
                final=False,
            )
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/weather-agent/v1/message:stream",
                json={
                    "role": "user",
                    "messageId": "msg-123",
                    "parts": [{"kind": "text", "text": "Hello"}],
                },
            )

        # Should have two events
        content = response.text
        events = [line for line in content.split("\n") if line.startswith("data: ")]
        assert len(events) == 2
        assert '"working"' in events[0]
        assert '"completed"' in events[1]


class TestGatewayServerJsonRpc:
    """Tests for JSON-RPC endpoint."""

    @pytest.fixture
    def server_with_callback(self) -> tuple[GatewayServer, AsyncMock]:
        """Create server with mock callback."""
        callback = AsyncMock()
        peer = make_peer("uuid-weather", "Weather Agent")
        peers = {"weather-agent": peer}
        peers_by_uuid = {"uuid-weather": peer}
        server = GatewayServer(
            peers=peers,
            peers_by_uuid=peers_by_uuid,
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=callback,
        )
        return server, callback

    def test_jsonrpc_returns_404_for_unknown_peer(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return 404 JSON-RPC error for unknown peer."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/unknown",
            json={
                "jsonrpc": "2.0",
                "id": "req-123",
                "method": "message/send",
                "params": {"message": {"role": "user", "parts": []}},
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32001
        assert "not found" in data["error"]["message"].lower()

    def test_jsonrpc_returns_error_for_unknown_method(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return error for unknown JSON-RPC method."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "req-123",
                "method": "unknown/method",
                "params": {},
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32601
        assert "unknown/method" in data["error"]["message"]
        assert data["id"] == "req-123"

    def test_jsonrpc_send_returns_task_result(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return Task result for message/send."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/weather-agent",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-123",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "role": "user",
                            "messageId": "msg-123",
                            "parts": [{"kind": "text", "text": "Hello"}],
                        }
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "req-123"
        assert data["result"]["id"] == "task-123"
        assert data["result"]["contextId"] == "ctx-123"

    def test_jsonrpc_stream_returns_sse(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should return SSE response for message/stream."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-123",
                contextId="ctx-123",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/weather-agent",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-123",
                    "method": "message/stream",
                    "params": {
                        "message": {
                            "role": "user",
                            "messageId": "msg-123",
                            "parts": [{"kind": "text", "text": "Hello"}],
                        }
                    },
                },
            )

        # Should be SSE format with JSON-RPC wrapped events
        assert "text/event-stream" in response.headers["content-type"]
        content = response.text
        assert content.startswith("data: ")
        assert '"jsonrpc":' in content
        assert "2.0" in content
        assert '"id":' in content
        assert "req-123" in content
        assert '"taskId":' in content
        assert "task-123" in content

    def test_jsonrpc_send_returns_invalid_params_for_bad_message(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """JSON-RPC send should return an invalid params error for bad message data."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "req-invalid",
                "method": "message/send",
                "params": {"message": {"parts": []}},
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == "req-invalid"
        assert data["error"]["code"] == -32602
        assert data["error"]["message"] == "Invalid params"
        assert data["error"]["data"]

    def test_jsonrpc_stream_returns_invalid_params_for_bad_message(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """JSON-RPC stream should surface invalid params as SSE instead of crashing."""
        server, _ = server_with_callback
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "req-invalid-stream",
                "method": "message/stream",
                "params": {"message": {"parts": []}},
            },
        )

        assert response.status_code == 400
        assert "text/event-stream" in response.headers["content-type"]
        content = response.text
        assert '"code": -32602' in content
        assert "req-invalid-stream" in content

    def test_jsonrpc_resolves_by_uuid(
        self, server_with_callback: tuple[GatewayServer, AsyncMock]
    ) -> None:
        """Should resolve peer by UUID fallback."""
        server, _ = server_with_callback

        async def mock_callback(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            yield TaskStatusUpdateEvent(
                taskId="task-456",
                contextId="ctx-456",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server.on_request = mock_callback
        app = server._build_app()
        client = TestClient(app)

        with client:
            response = client.post(
                "/agents/uuid-weather",  # Using UUID instead of slug
                json={
                    "jsonrpc": "2.0",
                    "id": "req-456",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "role": "user",
                            "messageId": "msg-456",
                            "parts": [{"kind": "text", "text": "Hello"}],
                        }
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["id"] == "task-456"


class TestGatewayHttpContextRouting:
    """Tests that gateway HTTP endpoint routes same contextId to same room."""

    @pytest.fixture
    def server_with_context_tracking(self) -> tuple[GatewayServer, dict]:
        """Create server that tracks context_id from requests."""
        weather = make_peer("uuid-weather", "Weather Agent")
        peers = {"weather-agent": weather}
        peers_by_uuid = {"uuid-weather": weather}

        # Track which context_ids are received
        context_ids_received: list[str | None] = []

        async def mock_on_request(
            peer_id: str, message: A2AMessage
        ) -> AsyncIterator[TaskStatusUpdateEvent]:
            ctx = message.context_id
            context_ids_received.append(ctx)

            yield TaskStatusUpdateEvent(
                taskId="task-1",
                contextId=ctx or "generated-ctx",
                status=TaskStatus(state=TaskState.completed),
                final=True,
            )

        server = GatewayServer(
            peers=peers,
            peers_by_uuid=peers_by_uuid,
            gateway_url="http://localhost:10000",
            port=10000,
            on_request=mock_on_request,
        )
        return server, context_ids_received

    def test_json_rpc_same_context_id_twice_tracked(
        self, server_with_context_tracking: tuple[GatewayServer, list]
    ) -> None:
        """JSON-RPC: Same contextId twice should be received consistently."""
        server, context_ids = server_with_context_tracking
        app = server._build_app()
        client = TestClient(app)

        # First request with contextId
        response1 = client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "messageId": "msg-1",
                        "contextId": "ctx-shared-123",
                        "parts": [{"kind": "text", "text": "First message"}],
                    }
                },
            },
        )

        # Second request with SAME contextId
        response2 = client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "messageId": "msg-2",
                        "contextId": "ctx-shared-123",
                        "parts": [{"kind": "text", "text": "Second message"}],
                    }
                },
            },
        )

        assert response1.status_code == 200
        assert response2.status_code == 200
        # Both requests should have same context_id
        assert len(context_ids) == 2
        assert context_ids[0] == "ctx-shared-123"
        assert context_ids[1] == "ctx-shared-123"

    def test_json_rpc_different_context_ids_tracked(
        self, server_with_context_tracking: tuple[GatewayServer, list]
    ) -> None:
        """JSON-RPC: Different contextIds should be tracked separately."""
        server, context_ids = server_with_context_tracking
        app = server._build_app()
        client = TestClient(app)

        client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "messageId": "m1",
                        "contextId": "ctx-a",
                        "parts": [{"kind": "text", "text": "A"}],
                    }
                },
            },
        )
        client.post(
            "/agents/weather-agent",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "messageId": "m2",
                        "contextId": "ctx-b",
                        "parts": [{"kind": "text", "text": "B"}],
                    }
                },
            },
        )

        # Two different contexts received
        assert len(context_ids) == 2
        assert "ctx-a" in context_ids
        assert "ctx-b" in context_ids

    def test_legacy_stream_endpoint_receives_context_id(
        self, server_with_context_tracking: tuple[GatewayServer, list]
    ) -> None:
        """Legacy REST stream endpoint should receive contextId."""
        server, context_ids = server_with_context_tracking
        app = server._build_app()
        client = TestClient(app)

        response = client.post(
            "/agents/weather-agent/v1/message:stream",
            json={
                "role": "user",
                "messageId": "msg-123",
                "contextId": "ctx-legacy-456",
                "parts": [{"kind": "text", "text": "Hello from legacy"}],
            },
        )

        assert response.status_code == 200
        assert len(context_ids) == 1
        assert context_ids[0] == "ctx-legacy-456"
