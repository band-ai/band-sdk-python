from __future__ import annotations

from typing import Any, ClassVar

from band_sdk_core import DeadReason

from band.client.streaming import (
    SupersedePayload,
    WebSocketClient,
    WebSocketDisconnectReason,
)


class TestClientSupersede:
    async def test_supersede_event_records_terminal_reason_and_disables_reconnect(self):
        """agent_control supersede should record the server reason and stop reconnects."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        client.client = type("MockPhoenix", (), {"auto_reconnect": True})()
        received_payload = None

        async def on_supersede(payload: SupersedePayload):
            nonlocal received_payload
            received_payload = payload
            outcome = client.handle_supersede(payload.retryable, payload.retry_after)
            client.record_terminal_disconnect(payload.to_disconnect_reason(outcome))

        class MockMessage:
            event = "supersede"
            payload: ClassVar[dict[str, Any]] = {
                "reason": "session.already_connected",
                "message": "This connection has been superseded by a newer session for this agent.",
                "retryable": False,
                "retry_after": 15,
                "target_socket_id": "agent_socket:agent-123",
                "correlation_id": "evict-123",
            }

        await client._handle_events(MockMessage(), {"supersede": on_supersede})

        assert isinstance(received_payload, SupersedePayload)
        assert client.client.auto_reconnect is False
        assert client.last_disconnect_reason == WebSocketDisconnectReason(
            reason="session.already_connected",
            message="This connection has been superseded by a newer session for this agent.",
            retryable=False,
            retry_after=15,
            target_socket_id="agent_socket:agent-123",
            correlation_id="evict-123",
            dead_reason=DeadReason.Classified,
        )
