"""Pytest fixtures shared by the ClaudeSDKAdapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Any
from unittest.mock import patch

import looptime
import pytest
import pytest_asyncio

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import ClaudeRoom


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    """The scripted Claude CLI behind every ``ClaudeSDKClient`` the test opens.

    The transport factory is the one seam patched: past it the SDK would spawn the
    real CLI subprocess.
    """
    claude = FakeClaude()
    with patch(
        "band.integrations.claude_sdk.session_manager.create_transport",
        claude.transport,
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
    adapters: list[tuple[ClaudeSDKAdapter, AbstractContextManager[None]]] = []

    async def open_room(
        config: ClaudeSDKAdapterConfig | None = None,
        *,
        room_id: str = "room-1",
        **adapter_kwargs: Any,
    ) -> ClaudeRoom:
        adapter = ClaudeSDKAdapter(config, **adapter_kwargs)
        await adapter.on_started("Test Agent", "An agent under test")
        loop = asyncio.get_running_loop()
        cleanup_clock = (
            looptime.enabled(strict=True)
            if isinstance(loop, looptime.LoopTimeEventLoop) and loop.looptime_on
            else nullcontext()
        )
        adapters.append((adapter, cleanup_clock))
        return ClaudeRoom(adapter, claude, room_id)

    yield open_room
    # Uvicorn's pending timers must finish on the clock that scheduled them.
    for adapter, cleanup_clock in adapters:
        with cleanup_clock:
            await adapter.cleanup_all()
