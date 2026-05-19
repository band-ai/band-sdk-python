"""Gateway client for calling A2A Gateway peers.

This module provides a client wrapper for calling Thenvoi platform peers
via the A2A Gateway using the standard A2A protocol.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    MessageSendParams,
    Part,
    SendMessageRequest,
    SendMessageSuccessResponse,
    TextPart,
)

logger = logging.getLogger(__name__)


def extract_text_from_parts(parts: list[Part]) -> str:
    """Extract text content from A2A message parts."""
    texts = []
    for part in parts:
        if isinstance(part.root, TextPart):
            texts.append(part.root.text)
    return " ".join(texts)


class GatewayClient:
    """Client for calling A2A Gateway peers.

    This client wraps the A2A SDK's client to provide a simple interface
    for calling peers exposed by the A2A Gateway.

    Example:
        client = GatewayClient("http://localhost:10000")
        response = await client.call_peer("weather", "What's the weather in NYC?")
    """

    def __init__(self, gateway_url: str, timeout: float = 60.0):
        """Initialize the gateway client.

        Args:
            gateway_url: Base URL of the A2A Gateway (e.g., "http://localhost:10000")
            timeout: Request timeout in seconds
        """
        self.gateway_url = gateway_url.rstrip("/")
        self.timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def list_peers(self) -> list[dict]:
        """Fetch list of available peers from gateway.

        Returns:
            List of peer dicts with id, name, description
        """
        http_client = await self._get_http_client()
        try:
            response = await http_client.get(f"{self.gateway_url}/peers")
            response.raise_for_status()
            data = response.json()
            return data.get("peers", [])
        except Exception as e:
            logger.warning("Could not fetch peers from gateway: %s", e)
            return []

    async def discover_peer(self, peer_id: str) -> bool:
        """Check if a peer is available via the gateway.

        Args:
            peer_id: The ID of the peer to check

        Returns:
            True if the peer is available, False otherwise
        """
        peer_url = f"{self.gateway_url}/agents/{peer_id}"
        http_client = await self._get_http_client()

        try:
            resolver = A2ACardResolver(http_client, peer_url)
            await resolver.get_agent_card()
            return True
        except Exception as e:
            logger.debug("Peer %s not available: %s", peer_id, e)
            return False

    async def call_peer(
        self,
        peer_id: str,
        message: str,
        context_id: str | None = None,
    ) -> str:
        """Call a peer via the A2A Gateway.

        Args:
            peer_id: The ID of the peer to call (e.g., "weather", "servicenow")
            message: The message to send to the peer
            context_id: Optional context ID for conversation continuity

        Returns:
            The peer's response text

        Raises:
            RuntimeError: If the peer is not available or the call fails
        """
        peer_url = f"{self.gateway_url}/agents/{peer_id}"
        http_client = await self._get_http_client()

        logger.info("Calling peer '%s' via gateway: %s", peer_id, peer_url)

        try:
            # Resolve agent card
            resolver = A2ACardResolver(http_client, peer_url)
            card = await resolver.get_agent_card()
            logger.debug("Resolved agent card for peer '%s': %s", peer_id, card.name)

            # Create A2A client
            client = A2AClient(http_client, card, url=peer_url)

            # Build message
            message_id = str(uuid4())
            ctx_id = context_id or str(uuid4())

            request = SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(
                    message={
                        "role": "user",
                        "parts": [{"kind": "text", "text": message}],
                        "messageId": message_id,
                        "contextId": ctx_id,
                    }
                ),
            )

            # Send message and get response
            response = await client.send_message(request)

            # Extract response text
            return self._extract_response(response)

        except Exception as e:
            error_msg = f"Failed to call peer '{peer_id}': {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _extract_response(self, response: SendMessageSuccessResponse) -> str:
        """Extract text response from A2A response.

        Args:
            response: The A2A send message response

        Returns:
            The extracted text response
        """
        # The response contains a Task with status and/or artifacts
        task = response.root.result

        # Try artifacts first (completed tasks)
        if task.artifacts:
            for artifact in task.artifacts:
                if artifact.parts:
                    text = extract_text_from_parts(artifact.parts)
                    if text:
                        return text

        # Fall back to status message
        if task.status and task.status.message:
            parts = task.status.message.parts
            if parts:
                return extract_text_from_parts(parts)

        return "No response from peer"

    async def __aenter__(self) -> GatewayClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
