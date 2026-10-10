"""preflight() runs a throwaway Claude CLI no room owns."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeSDKClient, CLIConnectionError, CLINotFoundError

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.harness import PreflightResult
from tests.adapters.claude_sdk.fakecli import FakeClaude


class Probes:
    """Every probe client the adapter opened, and how many it closed."""

    def __init__(self) -> None:
        self.opened: list[ClaudeSDKClient] = []
        self.closed = 0


@pytest.fixture
def probes(claude: FakeClaude) -> Iterator[Probes]:
    recorded = Probes()
    disconnect = ClaudeSDKClient.disconnect

    def open_probe(*, options: Any) -> ClaudeSDKClient:
        client = claude.client(options=options)
        recorded.opened.append(client)
        return client

    async def close_probe(client: ClaudeSDKClient) -> None:
        recorded.closed += 1
        await disconnect(client)

    with (
        patch("band.adapters.claude_sdk.ClaudeSDKClient", open_probe),
        patch.object(ClaudeSDKClient, "disconnect", close_probe),
    ):
        yield recorded


@pytest.mark.parametrize(
    ("error", "reason_part"),
    [
        (CLINotFoundError("claude not found"), "CLI not found"),
        (CLIConnectionError("exited early"), "did not complete its handshake"),
    ],
    ids=["missing-executable", "handshake"],
)
async def test_preflight_names_the_failure_and_closes(
    claude: FakeClaude, probes: Probes, error: Exception, reason_part: str
) -> None:
    claude.connect_error = error

    result = await ClaudeSDKAdapter().preflight()

    assert (result.ok, reason_part in (result.reason or "")) == (False, True)
    assert (len(probes.opened), probes.closed > 0) == (1, True)


async def test_preflight_cancellation_still_closes(
    claude: FakeClaude, probes: Probes
) -> None:
    claude.connect_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await ClaudeSDKAdapter().preflight()
    assert (len(probes.opened), probes.closed > 0) == (1, True)


async def test_preflight_creates_no_room_session(
    claude: FakeClaude, probes: Probes, tmp_path: Path
) -> None:
    adapter = ClaudeSDKAdapter(workspace_for_room=lambda room: str(tmp_path / room))
    await adapter.on_started("Claude", "coding agent")

    result = await adapter.preflight()

    assert result == PreflightResult.passed()
    assert adapter._session_manager is not None
    assert adapter._session_manager._sessions == {}
    assert adapter._workspaces.rooms == ()
    [session] = claude.sessions
    assert session.alive is False
    await adapter.cleanup_all()
