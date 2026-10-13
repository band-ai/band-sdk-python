"""The scripted room process most ACPClientAdapter tests run on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from acp.schema import NewSessionResponse

from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.integrations.acp.acp_toolkit.harness import FakeSpawn, make_acp_connection
from tests.integrations.acp.clientadapter.kit import adapter_on


@pytest.fixture
def mock_spawn() -> FakeSpawn:
    """The scripted connection each of the room's processes starts with."""
    conn = make_acp_connection()
    conn.new_session = AsyncMock(
        return_value=NewSessionResponse(session_id="acp-session-123")
    )
    return FakeSpawn(conn=conn)


@pytest_asyncio.fixture(loop_scope="function")
async def adapter_with_mocks(mock_spawn: FakeSpawn) -> AsyncIterator[ACPClientAdapter]:
    """An adapter whose room processes run on ``mock_spawn``'s connection,
    its rooms torn down after the test."""
    adapter = adapter_on(mock_spawn.conn)
    yield adapter
    await adapter.cleanup_all()
