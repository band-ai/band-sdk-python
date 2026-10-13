"""Regression: a burst of events must not fan unbounded concurrent forwards. With ``max_concurrent_forwards=N``, at most N forwards run at once even if N+K tasks are spawned together."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.bridge.kit import (
    build_runner,
    make_message_event,
)


class TestAgentRunnerConcurrencyCap:
    """Regression: a burst of events must not fan unbounded concurrent
    forwards. With ``max_concurrent_forwards=N``, at most N forwards run at
    once even if N+K tasks are spawned together.
    """

    async def test_semaphore_caps_in_flight_forwards(self) -> None:
        in_flight = 0
        peak = 0
        gate = asyncio.Event()

        class _BlockingForwarder:
            async def forward(self, payload: dict[str, Any]) -> None:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                try:
                    await gate.wait()
                finally:
                    in_flight -= 1

            async def close(self) -> None:
                return

        fwd = _BlockingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)  # type: ignore[arg-type]
        runner._forward_semaphore = asyncio.Semaphore(3)

        # Fire 10 forwards on distinct rooms so per-room locks don't serialize
        # them — only the semaphore can.
        tasks = [
            asyncio.create_task(
                runner._safe_handle_event(
                    make_message_event(message_id=f"m{i}", room_id=f"r{i}")
                )
            )
            for i in range(10)
        ]
        # Let the event loop start them all.
        for _ in range(5):
            await asyncio.sleep(0)

        assert peak == 3, f"expected peak=3, saw {peak}"

        gate.set()
        await asyncio.gather(*tasks)
