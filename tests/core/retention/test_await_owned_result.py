"""Owned provider gates stay referenced after an abandoned wait."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import cleanup
from band.core.retention.cleanup import await_owned_result
from tests.adapters.hold import Hold

pytestmark = pytest.mark.timeout(10)


class TestAwaitOwnedResult:
    @pytest.mark.looptime
    async def test_an_abandoned_gate_stays_held_until_it_finishes(self) -> None:
        gate = Hold()
        finished = asyncio.Event()

        async def ensure() -> str:
            try:
                await gate.wait()
                return "ready"
            finally:
                finished.set()

        with pytest.raises(TimeoutError, match="Band MCP server start"):
            await await_owned_result(
                ensure(), timeout=0.05, label="Band MCP server start"
            )
        held = [task for task in cleanup._held if not task.done()]
        assert len(held) == 1
        gate.released.set()
        assert await held[0] == "ready"
        assert held[0] not in cleanup._held
        assert finished.is_set()
