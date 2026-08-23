"""Test helpers for the runtime-injected platform connection."""

from __future__ import annotations

from band.core.types import PlatformConnection


def platform_connection_stub(agent_id: str = "test-agent-id") -> PlatformConnection:
    """A ``PlatformConnection`` with placeholder credentials for unit tests.

    Mirrors what ``Agent.start`` injects, so a test driving an adapter without
    the Band runtime can set ``adapter.platform = platform_connection_stub(...)``.
    """
    return PlatformConnection(
        agent_id=agent_id,
        api_key="test-api-key",
        rest_url="https://test.invalid",
        ws_url="wss://test.invalid/socket",
    )
