"""TestBandMcpBackends"""

from __future__ import annotations

import pytest

from band.testing import FakeAgentTools
from tests.integrations.mcpbackends.kit import ONE_TOOL
from tests.mcpclient import started_backend


class TestBandMcpBackends:
    @pytest.mark.asyncio
    async def test_create_http_backend(self) -> None:
        async with started_backend(
            room_bound=False,
            tool_definitions=[ONE_TOOL],
            get_tools=lambda _room_id: FakeAgentTools(),
        ) as backend:
            assert backend.allowed_tools == [f"mcp__band__{ONE_TOOL.name}"]
            assert backend.local_server.http_url.startswith("http://127.0.0.1:")
            assert backend.is_running

        assert backend.is_running is False
