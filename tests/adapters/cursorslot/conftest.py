"""Fixtures for the Cursor provider-slot tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import pytest_asyncio

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import AcpSession, started_acp_adapter

OpenCursor = Callable[..., Awaitable[AcpSession]]


# The adapter's ACP runtimes live on the loop that started them, so setup, the
# test and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def open_cursor() -> AsyncIterator[OpenCursor]:
    """Start a Cursor adapter wired in process to a peer; all stop at teardown."""
    async with AsyncExitStack() as stack:

        async def open_session(agent: FakeACPAgent, **config: Any) -> AcpSession:
            adapter = CursorACPAdapter(CursorACPAdapterConfig(**config))
            return await stack.enter_async_context(started_acp_adapter(adapter, agent))

        yield open_session
