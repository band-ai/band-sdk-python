from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from thenvoi.integrations.mcp.backends import create_thenvoi_mcp_backend
from thenvoi.runtime.tools import iter_tool_definitions
from thenvoi.testing import FakeAgentTools


class TestThenvoiMcpBackends:
    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install thenvoi-sdk[claude_sdk])",
    )
    async def test_create_sdk_backend(self) -> None:
        tool_definitions = list(iter_tool_definitions(include_memory=False))[:1]

        backend = await create_thenvoi_mcp_backend(
            kind="sdk",
            tool_definitions=tool_definitions,
            get_tools=lambda _room_id: MagicMock(),
        )

        assert backend.kind == "sdk"
        assert backend.local_server is None
        assert backend.allowed_tools == [f"mcp__thenvoi__{tool_definitions[0].name}"]

    @pytest.mark.asyncio
    async def test_create_http_backend(self) -> None:
        tool_definitions = list(iter_tool_definitions(include_memory=False))[:1]
        tools = FakeAgentTools()

        backend = await create_thenvoi_mcp_backend(
            kind="http",
            tool_definitions=tool_definitions,
            get_tools=lambda room_id: tools if room_id == "room-123" else None,
        )

        try:
            assert backend.kind == "http"
            assert backend.local_server is backend.server
            assert backend.allowed_tools == [
                f"mcp__thenvoi__{tool_definitions[0].name}"
            ]
            assert backend.local_server is not None
            assert backend.local_server.http_url.startswith("http://127.0.0.1:")
        finally:
            await backend.stop()
