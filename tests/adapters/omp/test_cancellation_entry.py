from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    Launches,
    SwallowingResolver,
    checkpoint,
    launches,
    run_turn,
)

__all__ = ["launches"]


class TestCancellationEntry:
    @pytest.mark.asyncio
    async def test_a_cancellation_pending_at_entry_launches_nothing(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SwallowingResolver()
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolver, emit=()
        )

        async def caller():
            asyncio.current_task().cancel()  # requested, not yet delivered
            await run_turn(adapter, FakeAgentTools())

        with pytest.raises(asyncio.CancelledError):
            await asyncio.create_task(caller())

        assert resolver.calls == 0 and launches.runtimes == []
        assert not adapter._workspaces.rooms and not adapter._owners
        await run_turn(adapter, FakeAgentTools())  # explicit recovery
        assert len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_cancellation_handled_before_entry_does_not_stop_the_turn(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SwallowingResolver()
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolver, emit=()
        )

        async def caller():
            asyncio.current_task().cancel()
            try:
                await checkpoint()  # delivered and handled by the caller itself
            except asyncio.CancelledError:
                pass
            await run_turn(adapter, FakeAgentTools())

        await asyncio.create_task(caller())

        assert resolver.calls == 1 and len(launches.runtimes) == 1
        await adapter.cleanup_all()
