"""Pytest fixtures shared by the ClaudeSDKAdapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import ClaudeRoom


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    """The scripted Claude CLI behind every ``ClaudeSDKClient`` the test opens.

    The constructor is the one seam patched: past it the SDK would spawn the
    real CLI subprocess.
    """
    claude = FakeClaude()
    with patch(
        "band.integrations.claude_sdk.session_manager.ClaudeSDKClient", claude.client
    ):
        yield claude
    claude.assert_done()


# The adapter's session manager runs on the loop that started it, so setup,
# the test and teardown must share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def claude_room(
    claude: FakeClaude,
) -> AsyncIterator[Callable[..., Awaitable[ClaudeRoom]]]:
    """Open a room on a freshly started adapter; every adapter is torn down
    at the end, cancelling whatever its turns still wait on."""
    adapters: list[ClaudeSDKAdapter] = []

    async def open_room(room_id: str = "room-1", **adapter_config: Any) -> ClaudeRoom:
        adapter = ClaudeSDKAdapter(**adapter_config)
        await adapter.on_started("Test Agent", "An agent under test")
        adapters.append(adapter)
        return ClaudeRoom(adapter, claude, room_id)

    yield open_room
    for adapter in adapters:
        await adapter.cleanup_all()
