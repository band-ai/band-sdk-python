"""The scripted room process most ACPClientAdapter tests run on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.integrations.acp.acp_toolkit.harness import (
    FakeSpawn,
    scripted_session_spawn,
)
from tests.integrations.acp.clientadapter import kit


@pytest.fixture
def mock_spawn() -> FakeSpawn:
    """The scripted connection each of the room's processes starts with."""
    return scripted_session_spawn()


@pytest_asyncio.fixture(name="adapter_on", loop_scope="function")
async def torn_down_adapters() -> AsyncIterator[kit.AdapterOn]:
    """Builds adapters as ``kit.adapter_on`` does; every one is torn down
    after the test."""
    async with AsyncExitStack() as stack:

        def build(conn: AsyncMock, **settings: object) -> ACPClientAdapter:
            adapter = kit.adapter_on(conn, **settings)
            stack.push_async_callback(adapter.cleanup_all)
            return adapter

        yield build


@pytest.fixture
def adapter_with_mocks(
    mock_spawn: FakeSpawn, adapter_on: kit.AdapterOn
) -> ACPClientAdapter:
    """An adapter whose room processes run on ``mock_spawn``'s connection,
    its rooms torn down after the test."""
    return adapter_on(mock_spawn.conn)
