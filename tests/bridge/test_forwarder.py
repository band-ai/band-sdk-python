"""Tests for HTTPForwarder and AgentCoreForwarder."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bridge_core.config import AgentCoreTarget, HTTPTarget
from bridge_core.forwarder import (
    AgentCoreForwarder,
    HTTPForwarder,
    build_forwarder,
)


@pytest.fixture
def http_target() -> HTTPTarget:
    return HTTPTarget(url="https://example.com/invocations", timeout=10.0)


@pytest.fixture
def agentcore_target() -> AgentCoreTarget:
    return AgentCoreTarget(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/abc",
        region="us-east-1",
        timeout=10.0,
    )


# ---------------------------------------------------------------------------
# HTTPForwarder
# ---------------------------------------------------------------------------


class TestHTTPForwarder:
    async def test_posts_payload_as_json(self, http_target: HTTPTarget) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        forwarder = HTTPForwarder(http_target, httpx_client=mock_client)
        payload: dict[str, Any] = {"event_type": "message_created", "room_id": "r1"}
        await forwarder.forward(payload)

        mock_client.post.assert_awaited_once_with(http_target.url, json=payload)
        mock_response.raise_for_status.assert_called_once()

    async def test_raises_on_http_error(self, http_target: HTTPTarget) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_response
            )
        )
        mock_client.post = AsyncMock(return_value=mock_response)

        forwarder = HTTPForwarder(http_target, httpx_client=mock_client)
        with pytest.raises(httpx.HTTPStatusError):
            await forwarder.forward({})

    async def test_close_does_not_close_injected_client(
        self, http_target: HTTPTarget
    ) -> None:
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()

        forwarder = HTTPForwarder(http_target, httpx_client=mock_client)
        await forwarder.close()

        mock_client.aclose.assert_not_called()

    async def test_close_closes_owned_client(self, http_target: HTTPTarget) -> None:
        forwarder = HTTPForwarder(http_target)
        # Trigger lazy client creation
        mock_aclose = AsyncMock()
        owned = MagicMock()
        owned.aclose = mock_aclose
        forwarder._client = owned

        await forwarder.close()
        mock_aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# AgentCoreForwarder
# ---------------------------------------------------------------------------


class TestAgentCoreForwarder:
    async def test_invokes_runtime_with_payload(
        self, agentcore_target: AgentCoreTarget
    ) -> None:
        mock_body = MagicMock()
        mock_body.read = MagicMock(return_value=b"")
        mock_body.close = MagicMock()
        mock_client = MagicMock()
        mock_client.invoke_agent_runtime = MagicMock(
            return_value={"response": mock_body}
        )

        forwarder = AgentCoreForwarder(agentcore_target, boto3_client=mock_client)
        payload = {"event_type": "message_created", "room_id": "r-uuid"}
        await forwarder.forward(payload)

        mock_client.invoke_agent_runtime.assert_called_once()
        call_kwargs = mock_client.invoke_agent_runtime.call_args.kwargs
        assert call_kwargs["agentRuntimeArn"] == agentcore_target.runtime_arn
        assert call_kwargs["runtimeSessionId"] == "room-r-uuid"
        assert call_kwargs["contentType"] == "application/json"
        # Payload is JSON-encoded
        assert json.loads(call_kwargs["payload"].decode("utf-8")) == payload
        mock_body.close.assert_called_once()

    async def test_session_id_falls_back_to_agent_id_without_room(
        self, agentcore_target: AgentCoreTarget
    ) -> None:
        mock_body = MagicMock()
        mock_body.read = MagicMock(return_value=b"")
        mock_body.close = MagicMock()
        mock_client = MagicMock()
        mock_client.invoke_agent_runtime = MagicMock(
            return_value={"response": mock_body}
        )

        forwarder = AgentCoreForwarder(agentcore_target, boto3_client=mock_client)
        await forwarder.forward({"event_type": "contact_added", "agent_id": "agent-x"})

        assert (
            mock_client.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"]
            == "agent-agent-x"
        )

    async def test_timeout_raises(self, agentcore_target: AgentCoreTarget) -> None:
        # invoke takes longer than the timeout
        def _slow(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            import time

            time.sleep(0.2)
            return {
                "response": MagicMock(
                    read=MagicMock(return_value=b""), close=MagicMock()
                )
            }

        target = AgentCoreTarget(
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/a",
            region="us-east-1",
            timeout=0.05,
        )
        mock_client = MagicMock()
        mock_client.invoke_agent_runtime = _slow

        forwarder = AgentCoreForwarder(target, boto3_client=mock_client)
        with pytest.raises(TimeoutError):
            await forwarder.forward({"room_id": "r"})

    def test_derive_session_id_prefers_room(self) -> None:
        assert (
            AgentCoreForwarder._derive_session_id({"room_id": "r1", "agent_id": "a"})
            == "room-r1"
        )

    def test_derive_session_id_falls_back_to_agent(self) -> None:
        assert AgentCoreForwarder._derive_session_id({"agent_id": "a"}) == "agent-a"

    def test_derive_session_id_default(self) -> None:
        assert AgentCoreForwarder._derive_session_id({}) == "default"


# ---------------------------------------------------------------------------
# build_forwarder
# ---------------------------------------------------------------------------


class TestBuildForwarder:
    def test_builds_http(self, http_target: HTTPTarget) -> None:
        f = build_forwarder(http_target)
        assert isinstance(f, HTTPForwarder)

    def test_builds_agentcore(self, agentcore_target: AgentCoreTarget) -> None:
        f = build_forwarder(agentcore_target)
        assert isinstance(f, AgentCoreForwarder)
