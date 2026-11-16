"""Owned provider gates stay referenced after an abandoned wait."""

from __future__ import annotations

import asyncio

import pytest

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

        held = asyncio.create_task(ensure())
        with pytest.raises(TimeoutError, match="Band MCP server start"):
            await await_owned_result(held, timeout=0.05, label="Band MCP server start")
        assert not held.done()
        gate.released.set()
        assert await held == "ready"
        assert finished.is_set()

    @pytest.mark.looptime
    async def test_a_gate_that_finishes_before_the_wait_returns_its_result(
        self,
    ) -> None:
        gate = Hold()

        async def ensure() -> str:
            await gate.wait()
            return "ready"

        held = asyncio.create_task(ensure())
        gate.released.set()
        assert (
            await await_owned_result(held, timeout=1.0, label="Band MCP server start")
            == "ready"
        )
        assert held.done()

    @pytest.mark.looptime
    async def test_an_abandoned_coroutine_gate_stays_held_until_it_finishes(
        self,
    ) -> None:
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
        assert not finished.is_set()
        gate.released.set()
        await finished.wait()

    @pytest.mark.looptime
    async def test_a_coroutine_gate_that_finishes_before_the_wait_returns_its_result(
        self,
    ) -> None:
        gate = Hold()

        async def ensure() -> str:
            await gate.wait()
            return "ready"

        gate.released.set()
        assert (
            await await_owned_result(
                ensure(), timeout=1.0, label="Band MCP server start"
            )
            == "ready"
        )
