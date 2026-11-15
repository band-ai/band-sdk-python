"""AgentCoreForwarder invokes the runtime per room and propagates failures."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import pytest
from bridge_core.config import AgentCoreTarget
from bridge_core.forwarder import (
    AgentCoreForwarder,
)


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

    def test_get_client_applies_matching_socket_timeouts(self) -> None:
        """Regression: the boto client must inherit ``target.timeout`` as its
        socket read timeout. Otherwise ``wait_for`` returning doesn't stop the
        underlying boto thread, so a still-running call can race the next
        forward for the same room.
        """
        target = AgentCoreTarget(
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/a",
            region="us-east-1",
            timeout=45.0,
        )
        forwarder = AgentCoreForwarder(target)

        # Patch ``boto3.client`` so we can assert on the exact ``Config`` our
        # code requested — botocore reshapes ``retries`` and merges env defaults
        # into ``client.meta.config`` after construction, so introspecting the
        # built client tests boto's normalization rather than our intent.
        with mock.patch("boto3.client") as boto_client:
            forwarder._get_client()

        cfg = boto_client.call_args.kwargs["config"]
        assert cfg.read_timeout == 45.0
        assert cfg.connect_timeout == 10  # min(timeout, 10)
        assert cfg.retries == {"max_attempts": 1}

    def test_derive_session_id_prefers_room(self) -> None:
        assert (
            AgentCoreForwarder._derive_session_id({"room_id": "r1", "agent_id": "a"})
            == "room-r1"
        )

    def test_derive_session_id_falls_back_to_agent(self) -> None:
        assert AgentCoreForwarder._derive_session_id({"agent_id": "a"}) == "agent-a"

    def test_derive_session_id_default(self) -> None:
        assert AgentCoreForwarder._derive_session_id({}) == "default"
