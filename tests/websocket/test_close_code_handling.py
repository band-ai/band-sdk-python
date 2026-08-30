"""Close-code handling for the agent WebSocket connection.

Platform contract (thenvoi-platform ``spec/websocket-api.draft.md``, the
``agent_control`` channel): the ``supersede`` push is terminal
(``retryable: false``) — after receiving it the SDK must stay down no matter
which WebSocket close code is observed when the platform then drops the
socket. Since Phoenix 1.8 the platform closes evicted agent sockets with
close code 1001 ("going away"), and the push can be missed entirely when the
close outruns the platform's short drain window — in that case the close
code itself is the SDK's only signal, so its classification is pinned here
(literal 1001, mirroring the platform's own close-code pin).

These tests drive the real PHXChannelsClient supervisor against an
in-process fake Phoenix server, so the assertions cover the actual
reconnect loop rather than a mock of it.
"""

from __future__ import annotations

import asyncio
import json
import logging

from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.client_types import ReconnectPolicy
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from band.client.streaming import SupersedePayload, WebSocketClient

logger = logging.getLogger(__name__)

# Payload shape from the platform spec (Supersede Payload).
SUPERSEDE_PAYLOAD: dict = {
    "reason": "session.already_connected",
    "message": "This connection has been superseded by a newer session for this agent.",
    "retryable": False,
    "retry_after": 30,
    "target_socket_id": "agent_socket:agent-123",
    "correlation_id": "evict-abc123",
}

# Generous ceiling on the client's first reconnect delay (base 0.5s, factor 2,
# equal jitter → ≤1s for the first attempt). If no reconnect landed within
# this window, the supervisor has stopped.
RECONNECT_GRACE_S = 2.0


class FakePhoenixServer:
    """Minimal Phoenix Channels V2 server: acks joins, pushes, closes."""

    def __init__(self) -> None:
        self.connection_count = 0
        # topic -> join_ref of the current subscription; pushes must carry it
        # (the client drops pushes whose join_ref doesn't match the join).
        self.joined_topics: dict[str, str] = {}
        self._current: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._server = None
        self.url = ""

    async def __aenter__(self) -> "FakePhoenixServer":
        self._server = await serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/api/v1/socket/websocket"
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        self.connection_count += 1
        self._current = ws
        self._connected.set()
        try:
            async for raw in ws:
                join_ref, ref, topic, event, _payload = json.loads(raw)
                if event in {"phx_join", "phx_leave"}:
                    reply = [
                        join_ref,
                        ref,
                        topic,
                        "phx_reply",
                        {"status": "ok", "response": {}},
                    ]
                    await ws.send(json.dumps(reply))
                    if event == "phx_join":
                        self.joined_topics[topic] = join_ref
                elif event == "heartbeat":
                    reply = [
                        None,
                        ref,
                        "phoenix",
                        "phx_reply",
                        {"status": "ok", "response": {}},
                    ]
                    await ws.send(json.dumps(reply))
        except ConnectionClosed:
            logger.debug("Fake server connection closed")

    async def wait_for_connection(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        self._connected.clear()

    async def push(self, topic: str, event: str, payload: dict) -> None:
        assert self._current is not None
        join_ref = self.joined_topics.get(topic)
        await self._current.send(json.dumps([join_ref, None, topic, event, payload]))

    async def close_current(self, code: int, reason: str = "") -> None:
        assert self._current is not None
        await self._current.close(code=code, reason=reason)

    def abort_current(self) -> None:
        """Drop the TCP connection without a close frame (abnormal close)."""
        assert self._current is not None
        self._current.transport.abort()


async def test_supersede_push_is_terminal_regardless_of_close_code():
    """After the supersede push, the SDK stays down even on a retryable close code.

    The server closes with 1011 (server error), which the reconnect
    classification would otherwise retry — the push must win.
    """
    async with FakePhoenixServer() as server:
        superseded = asyncio.Event()
        ws = WebSocketClient(server.url, "test-key", "agent-123")

        async def on_supersede(payload: SupersedePayload) -> None:
            # Mirrors BandLink._on_supersede: the push is authoritative.
            ws.record_terminal_disconnect(payload.to_disconnect_reason())
            superseded.set()

        async with ws:
            await server.wait_for_connection()
            await ws.join_agent_control_channel("agent-123", on_supersede=on_supersede)
            assert "agent_control:agent-123" in server.joined_topics

            await server.push("agent_control:agent-123", "supersede", SUPERSEDE_PAYLOAD)
            await asyncio.wait_for(superseded.wait(), timeout=5.0)

            await server.close_current(1011, "internal error")
            await asyncio.sleep(RECONNECT_GRACE_S)

            assert server.connection_count == 1, (
                "SDK reconnected after terminal supersede"
            )
            assert ws.last_disconnect_reason is not None
            assert ws.last_disconnect_reason.retryable is False
            assert ws.last_disconnect_reason.reason == "session.already_connected"


async def test_missed_supersede_close_1001_does_not_reconnect():
    """Eviction close code 1001 keeps the SDK down when the push was missed.

    Phoenix 1.8 closes evicted agent sockets with 1001. If the socket closes
    before the supersede push is delivered, the close code is the only
    signal — the SDK must not reconnect on it (pinned literal, matching the
    platform's close-code pin).
    """
    async with FakePhoenixServer() as server:
        supersede_calls: list[SupersedePayload] = []
        ws = WebSocketClient(server.url, "test-key", "agent-123")

        async def on_supersede(payload: SupersedePayload) -> None:
            supersede_calls.append(payload)

        async with ws:
            await server.wait_for_connection()
            await ws.join_agent_control_channel("agent-123", on_supersede=on_supersede)

            await server.close_current(1001, "going away")
            await asyncio.sleep(RECONNECT_GRACE_S)

            assert server.connection_count == 1, (
                "SDK reconnected on eviction close 1001"
            )
            assert not supersede_calls
            assert ws.last_disconnect_reason is None


async def test_abnormal_close_without_supersede_reconnects():
    """A close with no code (abnormal drop) still reconnects — the accepted worst case.

    When neither the supersede push nor a 1000/1001 close frame arrives (for
    example an intermediary drops the TCP connection), the SDK cannot know it
    was superseded and retries. The platform bounds this churn by
    re-superseding and rate-limiting reconnects after an eviction.
    """
    async with FakePhoenixServer() as server:
        ws = WebSocketClient(server.url, "test-key", "agent-123")

        async def on_supersede(payload: SupersedePayload) -> None:
            return None

        async with ws:
            await server.wait_for_connection()
            await ws.join_agent_control_channel("agent-123", on_supersede=on_supersede)

            server.joined_topics.clear()
            server.abort_current()
            await server.wait_for_connection(timeout=5.0)

            assert server.connection_count == 2
            # Let the rejoin settle before shutdown so teardown doesn't race
            # the in-flight reconnect.
            async with asyncio.timeout(5.0):
                while "agent_control:agent-123" not in server.joined_topics:
                    await asyncio.sleep(0.05)


def test_default_reconnect_policy_treats_eviction_closes_as_terminal():
    """Pin the close-code classification the SDK relies on.

    WebSocketClient constructs PHXChannelsClient without a custom
    ReconnectPolicy, so these defaults are load-bearing: 1000/1001 must not
    reconnect (eviction path), while codeless and abnormal closes may.
    """
    assert ReconnectPolicy().reconnect_on_normal_close is False

    client = PHXChannelsClient("ws://localhost/api/v1/socket/websocket", "test-key")
    assert client._classify_disconnect(1000, "").should_reconnect is False
    assert client._classify_disconnect(1001, "going away").should_reconnect is False
    # Codeless / abnormal closes still reconnect (see the worst-case test above).
    assert client._classify_disconnect(None, "").should_reconnect is True
    assert client._classify_disconnect(1006, "").should_reconnect is True
