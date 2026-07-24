"""Unit tests for the agent.control WebSocket payload model."""

import pytest
from pydantic import ValidationError

from band.client.streaming import AgentControlPayload
from band.client.streaming.client import _PAYLOAD_MODELS


class TestAgentControlPayload:
    """Tests for AgentControlPayload model."""

    @pytest.mark.parametrize("mode", ["interrupt", "stop", "play"])
    def test_valid_modes(self, mode):
        """Should accept each of the three control modes."""
        payload = AgentControlPayload(
            type="agent.control",
            mode=mode,
            scope="agent",
            agent_id="agent-123",
            execution_id="exec-1",
            room_id="room-1",
            reason="user_requested",
            correlation_id="ctl-abc",
        )
        assert payload.mode == mode
        assert payload.scope == "agent"
        assert payload.agent_id == "agent-123"
        assert payload.execution_id == "exec-1"
        assert payload.room_id == "room-1"
        assert payload.correlation_id == "ctl-abc"

    def test_room_scope(self):
        """Should accept room scope."""
        payload = AgentControlPayload(
            mode="interrupt",
            scope="room",
            agent_id="agent-123",
            room_id="room-9",
        )
        assert payload.scope == "room"
        assert payload.room_id == "room-9"

    def test_null_room_and_execution(self):
        """Agent-scoped fan-out: room_id and execution_id may be omitted/null."""
        payload = AgentControlPayload(
            mode="stop",
            scope="agent",
            agent_id="agent-123",
        )
        assert payload.room_id is None
        assert payload.execution_id is None
        assert payload.reason is None
        assert payload.correlation_id is None

    def test_bad_mode_rejected(self):
        """Should reject unknown modes (e.g. legacy 'pause')."""
        with pytest.raises(ValidationError):
            AgentControlPayload(mode="pause", scope="agent", agent_id="a")

    def test_bad_scope_rejected(self):
        """Should reject unknown scopes."""
        with pytest.raises(ValidationError):
            AgentControlPayload(mode="stop", scope="cluster", agent_id="a")

    def test_missing_agent_id_rejected(self):
        """Should require agent_id."""
        with pytest.raises(ValidationError):
            AgentControlPayload(mode="stop", scope="agent")

    def test_extra_fields_allowed(self):
        """Should allow extra fields for forward compatibility."""
        payload = AgentControlPayload(
            mode="play",
            scope="agent",
            agent_id="agent-123",
            future_field="allowed",
        )
        assert payload.agent_id == "agent-123"

    def test_registered_in_payload_models(self):
        """The dispatcher must know how to parse agent.control events."""
        assert _PAYLOAD_MODELS.get("agent.control") is AgentControlPayload
