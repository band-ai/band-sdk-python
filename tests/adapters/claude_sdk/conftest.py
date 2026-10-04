"""Pytest fixtures shared by the ClaudeSDKAdapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import nullcontext
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
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

    async def open_room(
        config: ClaudeSDKAdapterConfig | None = None,
        *,
        room_id: str = "room-1",
        **adapter_kwargs: Any,
    ) -> ClaudeRoom:
        adapter = ClaudeSDKAdapter(config, **adapter_kwargs)
        await adapter.on_started("Test Agent", "An agent under test")
        adapters.append(adapter)
        return ClaudeRoom(adapter, claude, room_id)

    yield open_room
    # looptime is only on for the test body. Teardown runs on the real clock,
    # so a uvicorn sleep scheduled at virtual T waits ~T of wall time on a
    # fresh CI runner (uptime < T) and hits pytest-timeout.
    loop = asyncio.get_running_loop()
    enable_looptime = getattr(loop, "looptime_enabled", None)
    already_on = getattr(loop, "looptime_on", True)
    with (
        enable_looptime()
        if enable_looptime is not None and not already_on
        else nullcontext()
    ):
        for adapter in adapters:
            await adapter.cleanup_all()
