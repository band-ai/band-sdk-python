"""Virtual timers progress at the precision of each supported host clock."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import looptime
import pytest

from tests.toolkit.clock import NATIVE_CLOCK_RESOLUTIONS, binary_clock_resolution


class TestClock:
    @pytest.mark.parametrize("native_resolution", NATIVE_CLOCK_RESOLUTIONS)
    def test_timers_and_integral_deadlines_progress(
        self, monkeypatch: pytest.MonkeyPatch, native_resolution: float
    ) -> None:
        monkeypatch.setattr(
            "tests.toolkit.clock.get_clock_info",
            lambda name: SimpleNamespace(resolution=native_resolution),
        )
        loop = looptime.patch_event_loop(
            asyncio.SelectorEventLoop(), resolution=binary_clock_resolution()
        )
        # Model asyncio's native wakeup tolerance on every host.
        loop._clock_resolution = native_resolution

        async def elapsed_timers() -> list[float]:
            elapsed = []
            for delay in (0.05, 0.1, 0.2, 1.0, 1.8377008259506757, 5.0):
                start = loop.time()
                await asyncio.sleep(delay)
                elapsed.append(loop.time() - start)
            start = loop.time()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(5.0):
                    await asyncio.Future()
            elapsed.append(loop.time() - start)
            return elapsed

        try:
            assert loop.run_until_complete(elapsed_timers()) == pytest.approx(
                [0.05, 0.1, 0.2, 1.0, 1.8377008259506757, 5.0, 5.0],
                rel=0,
                abs=native_resolution,
            )
        finally:
            loop.close()
