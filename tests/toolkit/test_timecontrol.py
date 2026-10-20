"""A failed timing test must not leave a runnable clock task behind."""

from __future__ import annotations

import asyncio

import pytest

from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestTimeControl:
    async def test_exceptional_exit_joins_the_clock_before_another_context(
        self,
    ) -> None:
        before = asyncio.all_tasks()
        with pytest.raises(RuntimeError, match="test failed"):
            async with controlled_clock() as clock:
                clock.advance(1)
                raise RuntimeError("test failed")
        assert asyncio.all_tasks() == before
        async with controlled_clock() as clock:
            clock.advance(1)
        assert asyncio.all_tasks() == before
