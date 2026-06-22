"""Event forwarders — push raw Band WS events to agent endpoints.

Two transports:
- :class:`HTTPForwarder` POSTs JSON to a URL via httpx.
- :class:`AgentCoreForwarder` invokes a Bedrock AgentCore Runtime via boto3.

The bridge selects a forwarder per agent based on its :class:`Target`
discriminator. Forwarders are stateless about Band semantics — they just
transport a JSON payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from .config import AgentCoreTarget, HTTPTarget, Target

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB


class Forwarder(Protocol):
    """Forward a raw event payload to an agent endpoint."""

    async def forward(self, payload: dict[str, Any]) -> None:
        """Forward the event. Raises on transport errors."""
        ...

    async def close(self) -> None:
        """Release transport resources. Idempotent."""
        ...


class HTTPForwarder:
    """Forward events via HTTP POST.

    Uses an httpx async client. Response body is discarded — the bridge
    holds no Band logic, so any agent-side reply is not interpreted here.
    """

    def __init__(
        self,
        target: HTTPTarget,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._target = target
        self._client = httpx_client
        self._owns_client = httpx_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            try:
                import httpx as _httpx
            except ImportError:
                raise ImportError(
                    "httpx is required for HTTPForwarder. "
                    "Install with: pip install band-sdk[bridge]"
                )
            self._client = _httpx.AsyncClient(
                timeout=_httpx.Timeout(self._target.timeout)
            )
        return self._client

    async def forward(self, payload: dict[str, Any]) -> None:
        client = self._get_client()
        response = await client.post(self._target.url, json=payload)
        response.raise_for_status()

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


class AgentCoreForwarder:
    """Forward events via ``bedrock-agentcore:InvokeAgentRuntime``.

    Uses ``runtimeSessionId`` derived from the event's ``room_id`` to pin
    related events to the same AgentCore microVM (fresh session per room).
    """

    def __init__(
        self,
        target: AgentCoreTarget,
        boto3_client: Any | None = None,
    ) -> None:
        self._target = target
        self._client = boto3_client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ImportError(
                    "boto3 is required for AgentCoreForwarder. "
                    "Install with: pip install band-sdk[bridge_agentcore]"
                )
            # Match botocore's socket timeouts to ``self._target.timeout`` so
            # the underlying call actually ends when the bridge's ``wait_for``
            # gives up — otherwise the thread keeps running, ``forward`` would
            # have already released the per-room lock, and a follow-up event
            # for the same room can race the still-in-flight AgentCore call.
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self._target.region,
                config=Config(
                    connect_timeout=min(self._target.timeout, 10),
                    read_timeout=self._target.timeout,
                    retries={"max_attempts": 1},
                ),
            )
        return self._client

    @staticmethod
    def _derive_session_id(payload: dict[str, Any]) -> str:
        """Derive a runtimeSessionId from the event payload.

        Prefer room_id (fresh session per room). Fall back to agent_id for
        events without a room (e.g. contact events).
        """
        room_id = payload.get("room_id")
        if room_id:
            return f"room-{room_id}"
        agent_id = payload.get("agent_id")
        if agent_id:
            return f"agent-{agent_id}"
        return "default"

    async def forward(self, payload: dict[str, Any]) -> None:
        client = self._get_client()
        session_id = self._derive_session_id(payload)

        def _call() -> None:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=self._target.runtime_arn,
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="application/json, text/event-stream",
                payload=json.dumps(payload).encode("utf-8"),
            )
            # Drain the StreamingBody so the connection returns to the pool.
            # We discard the response — the bridge holds no Band logic.
            body = response.get("response") or response.get("body")
            if body is not None:
                try:
                    body.read(_MAX_RESPONSE_BYTES)
                finally:
                    body.close()

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_call),
                timeout=self._target.timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"AgentCore forward timed out after {self._target.timeout}s "
                f"(arn={self._target.runtime_arn})"
            ) from None

    async def close(self) -> None:
        # boto3 clients don't need explicit close.
        return


def build_forwarder(target: Target) -> Forwarder:
    """Return the forwarder for a given target."""
    if isinstance(target, HTTPTarget):
        return HTTPForwarder(target)
    if isinstance(target, AgentCoreTarget):
        return AgentCoreForwarder(target)
    raise ValueError(f"Unknown target type: {type(target).__name__}")
