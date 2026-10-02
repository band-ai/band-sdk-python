from __future__ import annotations

import pytest

from band.integrations.mcp.backends import create_band_mcp_backend
from band.runtime.tools import iter_tool_definitions
from band.testing import FakeAgentTools


class TestBandMcpBackends:
    @pytest.mark.asyncio
    async def test_create_http_backend(self) -> None:
        tool_definitions = list(iter_tool_definitions())[:1]
        tools = FakeAgentTools()

        backend = await create_band_mcp_backend(
            kind="http",
            tool_definitions=tool_definitions,
            get_tools=lambda room_id: tools if room_id == "room-123" else None,
        )

        try:
            assert backend.kind == "http"
            assert backend.allowed_tools == [f"mcp__band__{tool_definitions[0].name}"]
            assert backend.local_server.http_url.startswith("http://127.0.0.1:")
            assert backend.is_running
        finally:
            await backend.stop()
            assert backend.is_running is False
