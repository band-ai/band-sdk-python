"""The scripted room process most ACPClientAdapter tests run on."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
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


@pytest.fixture
def adapter_with_mocks(mock_spawn: FakeSpawn) -> ACPClientAdapter:
    """An adapter whose room processes run on ``mock_spawn``'s connection."""
    return adapter_on(mock_spawn.conn)
