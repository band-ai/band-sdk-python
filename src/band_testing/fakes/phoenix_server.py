"""Fake Phoenix WebSocket server for testing.

Requires the websocket optional dependency:
    uv add --dev websockets
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from websockets.asyncio.server import serve

if TYPE_CHECKING:
    from websockets.asyncio.server import Server, ServerConnection


class FakePhoenixServer:
    """Fake Phoenix WebSocket server for testing.

    Simulates the Phoenix Channels protocol for testing WebSocket clients.

    Example:
        @pytest_asyncio.fixture
        async def phoenix_server():
            server = FakePhoenixServer()
            await server.start()
            try:
                yield server
            finally:
                await server.stop()

        async def test_websocket(phoenix_server):
            async with PHXChannelsClient(phoenix_server.url) as client:
                await client.subscribe_to_topic("test-topic", callback)
                await phoenix_server.simulate_server_event(
                    "test-topic", "new_message", {"text": "hello"}
                )

    Attributes:
        host: Server host (default: localhost)
        port: Server port (default: 8765)
        valid_topics: Set of topics that can be subscribed to
        client_websocket: Current client connection (set after connection)
    """

    def __init__(self, host: str = "localhost", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self.valid_topics: set[str] = {
            "test-topic",
            "test-topic-b",
        }
        self.server: Server | None = None
        self.client_websocket: ServerConnection | None = None

    def is_valid_topic(self, topic: str) -> bool:
        """Check if a topic is valid for subscription."""
        return topic in self.valid_topics

    async def handler(self, websocket: ServerConnection) -> None:
        """Handle WebSocket connections and messages."""
        self.client_websocket = websocket
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.handle_message(data)
        except Exception:
            pass

    async def handle_message(self, data: Any) -> None:
        """Handle incoming Phoenix messages.

        Phoenix message format (array): [join_ref, msg_ref, topic, event, payload]
        """
        if self.client_websocket is None:
            return  # No client connected

        if not isinstance(data, list) or len(data) != 5:
            return  # Invalid message format

        join_ref, msg_ref, topic, event, payload = data

        if event == "phx_join":
            # Check if topic is valid before allowing join
            if self.is_valid_topic(topic):
                # Send successful join reply for valid topics
                reply = [
                    join_ref,
                    msg_ref,
                    topic,
                    "phx_reply",
                    {"status": "ok", "response": {}},
                ]
            else:
                # Send error reply for invalid topics
                reply = [
                    join_ref,
                    msg_ref,
                    topic,
                    "phx_reply",
                    {"status": "error", "response": {"reason": "unmatched topic"}},
                ]
            await self.client_websocket.send(json.dumps(reply))
        elif event == "phx_leave":
            # Send successful leave reply
            reply = [
                join_ref,
                msg_ref,
                topic,
                "phx_reply",
                {"status": "ok", "response": {}},
            ]
            await self.client_websocket.send(json.dumps(reply))

            # Also send phx_close message after successful leave
            close_message = [join_ref, join_ref, topic, "phx_close", {}]
            await self.client_websocket.send(json.dumps(close_message))

    async def simulate_server_event(
        self,
        topic: str,
        event: str,
        payload: dict[str, Any],
        join_ref: str | None = None,
    ) -> None:
        """Simulate a server event being sent to the client.

        Args:
            topic: The topic to send the event to
            event: The event name
            payload: The event payload
            join_ref: The join_ref to use (test controls this directly)
        """
        if self.client_websocket:
            message = [join_ref, None, topic, event, payload]
            await self.client_websocket.send(json.dumps(message))

    async def start(self) -> None:
        """Start the fake Phoenix server."""
        self.server = await serve(self.handler, self.host, self.port)

    async def stop(self) -> None:
        """Stop the fake Phoenix server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    @property
    def url(self) -> str:
        """Get the WebSocket URL for this server."""
        return f"ws://{self.host}:{self.port}/socket/websocket"
