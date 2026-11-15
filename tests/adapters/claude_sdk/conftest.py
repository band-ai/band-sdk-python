"""Pytest fixtures shared by the ClaudeSDKAdapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import looptime
import pytest
import pytest_asyncio

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from tests.adapters.claude_sdk.fakecli import FakeClaude, scripted_claude
from tests.adapters.claude_sdk.helpers import AGENT_DESCRIPTION, AGENT_NAME, ClaudeRoom


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    with scripted_claude() as claude:
        yield claude


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
        participants: list[dict[str, Any]] | None = None,
        **adapter_kwargs: Any,
    ) -> ClaudeRoom:
        adapter = ClaudeSDKAdapter(config, **adapter_kwargs)
        await adapter.on_started(AGENT_NAME, AGENT_DESCRIPTION)
        loop = asyncio.get_running_loop()
        cleanup_clock = (
            looptime.enabled(strict=True)
            if isinstance(loop, looptime.LoopTimeEventLoop) and loop.looptime_on
            else nullcontext()
        )
        adapters.append((adapter, cleanup_clock))
        return ClaudeRoom(adapter, claude, room_id, participants=participants)

    yield open_room
    # Uvicorn's pending timers must finish on the clock that scheduled them.
    for adapter, cleanup_clock in adapters:
        with cleanup_clock:
            await adapter.cleanup_all()
