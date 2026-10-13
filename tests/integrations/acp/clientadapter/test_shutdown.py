"""Graceful shutdown must release the adapter-wide subprocess/TCP connection."""

from __future__ import annotations

import pytest

from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    acp_adapter,
)
from tests.mcpbackends import backends_created_by


class TestACPClientAdapterShutdown:
    """Graceful shutdown must release the adapter-wide subprocess/TCP connection.

    ``Agent.stop()`` invokes ``cleanup_all()`` (not ``stop()``), so the teardown has
    to hang off ``cleanup_all`` or the transport spawned in ``on_started`` leaks.
    """

    @pytest.mark.asyncio
    async def test_cleanup_all_tears_down_the_transport(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            runtime = session.runtime()

            await session.adapter.cleanup_all()  # what Agent.stop() calls

            assert runtime.closed
            assert session.adapter._rooms == {}

    @pytest.mark.asyncio
    async def test_restart_after_a_full_stop_allows_backend_creation(self) -> None:
        """Agent.start() reuses the same adapter instance across a
        stop()-then-start() restart (and across a retry after a failed
        start -- both go through cleanup_all's final=True default). Rooms and
        the MCP backend must serve again, or a perfectly healthy restarted
        adapter can never answer or call a Band tool again."""
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("before the restart")

            await session.adapter.cleanup_all()  # Agent.stop(), final=True
            await session.adapter.on_started("Codex", "bridge")  # Agent.start()

            with backends_created_by() as starts:
                await session.adapter._mcp.ensure()
            await session.send("after the restart")

        assert len(starts.requested) == 1
        assert agent.session_ids() == ["fake-session-1", "fake-session-2"]
