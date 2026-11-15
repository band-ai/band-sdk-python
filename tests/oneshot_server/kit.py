"""Receiver environment and platform identity shared by the fixtures and the tests."""

from __future__ import annotations

from datetime import UTC, datetime

from band_rest import AgentMe, GetAgentMeResponse

CREDENTIALS = {
    "BAND_AGENT_ID": "agent-1",
    "BAND_API_KEY": "test-key",
    "ANTHROPIC_API_KEY": "test-provider",
}
PLATFORM_URL = "https://receiver-platform.test"
AGENT_ME_PATH = "/api/v1/agent/me"
REGISTERED = datetime(2026, 1, 1, tzinfo=UTC)
AGENT_ME = GetAgentMeResponse(
    data=AgentMe(
        handle="test/receiver",
        id=CREDENTIALS["BAND_AGENT_ID"],
        inserted_at=REGISTERED,
        name="Receiver",
        description="a receiver under test",
        owner_uuid="owner-1",
        updated_at=REGISTERED,
        feature_flags={},
    )
)
