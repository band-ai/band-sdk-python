"""Fixtures for the Cursor room-turn tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest_asyncio

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from tests.adapters.cursorturns.kit import SETTLE_S
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import AcpSession, started_acp_adapter

OpenCursor = Callable[..., Awaitable[AcpSession]]


@asynccontextmanager
async def cursor_session(
    agent: FakeACPAgent, **config: Any
) -> AsyncIterator[AcpSession]:
    """Own the real ACP resources on the calling test's loop."""
    adapter = CursorACPAdapter(
        CursorACPAdapterConfig(**{"turn_settle_timeout_s": SETTLE_S, **config})
    )
    async with started_acp_adapter(adapter, agent) as session:
        yield session


# The adapter's ACP runtimes live on the loop that started them, so setup, the
# test and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def open_cursor() -> AsyncIterator[OpenCursor]:
    """Start a Cursor adapter wired in process to a peer; all stop at teardown."""
    async with AsyncExitStack() as stack:

        async def open_session(agent: FakeACPAgent, **config: Any) -> AcpSession:
            return await stack.enter_async_context(cursor_session(agent, **config))

        yield open_session
