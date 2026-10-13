"""Tests for ACPClientAdapter.stop()."""

from __future__ import annotations

import pytest

from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.clientadapter.kit import CODEX, MOCK_ROOM
from tests.mcpbackends import backends_created_by, hold_backend


class TestACPClientAdapterStop:
    """Tests for ACPClientAdapter.stop()."""

    @pytest.mark.asyncio
    async def test_stop_closes_connection(self) -> None:
        """Should close every room's ACP process and the MCP backend."""
        agent = FakeACPAgent().will_say("ok")
        with backends_created_by():
            async with acp_adapter(
                agent, fake_agent_config(inject_band_tools=True)
            ) as session:
                await session.send("hello", room=MOCK_ROOM)
                runtime = session.runtime(MOCK_ROOM)

                await session.adapter.stop()

                assert runtime.closed
                assert runtime.client is None
                assert session.adapter._rooms == {}
                assert session.adapter._mcp.current is None

    @pytest.mark.asyncio
    async def test_stop_no_connection(self) -> None:
        """Should handle stop when not connected."""
        adapter = ACPClientAdapter(CODEX)
        backend = await hold_backend(adapter._mcp)

        await adapter.stop()

        assert backend.stop_calls == 1
