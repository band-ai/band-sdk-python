"""Gateway client for calling A2A Gateway peers."""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_message_text
from a2a.types import Message, Part, Role, SendMessageRequest, Task

from band.integrations.a2a.protocol import (
    apply_task_stream_event,
    task_response_text,
)

logger = logging.getLogger(__name__)


class GatewayClient:
    """Small client wrapper around the official A2A 1.1 client."""

    def __init__(self, gateway_url: str, timeout: float = 60.0):
        self.gateway_url = gateway_url.rstrip("/")
        self.timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def list_peers(self) -> list[dict]:
        http_client = await self._get_http_client()
        try:
            response = await http_client.get(f"{self.gateway_url}/peers")
            response.raise_for_status()
            return response.json().get("peers", [])
        except Exception as exc:
            logger.warning("Could not fetch peers from gateway: %s", exc)
            return []

    async def discover_peer(self, peer_id: str) -> bool:
        try:
            client = await self._create_client(peer_id)
            await client.close()
            return True
        except Exception as exc:
            logger.debug("Peer %s not available: %s", peer_id, exc)
            return False

    async def _create_client(self, peer_id: str):
        http_client = await self._get_http_client()
        factory = ClientFactory(ClientConfig(streaming=True, httpx_client=http_client))
        return await factory.create_from_url(f"{self.gateway_url}/agents/{peer_id}")

    async def call_peer(
        self,
        peer_id: str,
        message: str,
        context_id: str | None = None,
    ) -> str:
        logger.info("Calling peer '%s' via gateway", peer_id)
        try:
            client = await self._create_client(peer_id)
            request = SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id=str(uuid4()),
                    context_id=context_id or str(uuid4()),
                    parts=[Part(text=message)],
                )
            )
            task: Task | None = None
            async for event in client.send_message(request):
                if event.HasField("message"):
                    return get_message_text(event.message)
                task = apply_task_stream_event(task, event) or task
            return task_response_text(task) or "No response from peer"
        except Exception as exc:
            error_msg = f"Failed to call peer '{peer_id}': {exc}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from exc

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
