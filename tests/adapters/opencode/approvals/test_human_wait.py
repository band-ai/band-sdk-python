from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode.approvals.humanwait import HumanWait


class TestHumanWait:
    async def test_a_new_wait_is_idle(self) -> None:
        wait = HumanWait()

        await asyncio.wait_for(wait.wait_until_idle(), timeout=1)

        assert wait.is_idle
        assert wait.seconds == 0.0

    @pytest.mark.looptime
    async def test_parked_time_counts_until_released_then_is_banked(self) -> None:
        wait = HumanWait()

        wait.park()
        await asyncio.sleep(5)
        assert not wait.is_idle
        assert wait.seconds == 5

        wait.release()
        await asyncio.sleep(3)

        assert wait.is_idle
        assert wait.seconds == 5

    @pytest.mark.looptime
    async def test_parking_again_keeps_the_original_start(self) -> None:
        wait = HumanWait()

        wait.park()
        await asyncio.sleep(2)
        wait.park()
        await asyncio.sleep(2)

        assert wait.seconds == 4

    async def test_a_watcher_blocks_until_released(self) -> None:
        wait = HumanWait()
        wait.park()
        watcher = asyncio.create_task(wait.wait_until_idle())
        await asyncio.sleep(0)
        assert not watcher.done()

        wait.release()
        await watcher
