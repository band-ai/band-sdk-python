"""A cleanup reporting deadline leaves one owned backend close to join."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetention, RoomRetentionConfig
from band.integrations.mcp import SharedBandMCPBackend
from tests.integrations.mcpbackends.kit import one_tool_settings
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by


class TestCloseDeadline:
    @pytest.mark.looptime
    async def test_a_reporting_timeout_leaves_the_backend_close_owned(self) -> None:
        closing, release = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=release)
        retention = RoomRetentionConfig()
        rooms = RoomRetention(retention)
        with backends_created_by(backend):
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                await owner.ensure()
                stopping = asyncio.create_task(
                    owner.close_after(rooms.stop(), retention=retention)
                )
                try:
                    await closing.wait()
                    with pytest.raises(CleanupIncompleteError, match="MCP backend"):
                        await stopping
                    with pytest.raises(CleanupIncompleteError):
                        await owner.reopen()
                finally:
                    release.set()

                await owner.close_after(rooms.stop(), retention=retention)
                await owner.reopen()

        assert backend.stop_calls == 1
