"""Bridge control graceful degradation."""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.bridge.kit import (
    build_runner,
    control_signal,
    drain_control_tasks,
)


class TestControlGracefulDegradation:
    async def test_unknown_mode_combinations_never_raise(self) -> None:
        """Defensive: any well-formed payload must not raise, even degenerate
        combinations (e.g. play for a room with nothing pending)."""
        runner, _, link = build_runner()
        link.get_next_message = AsyncMock(return_value=None)

        for mode in ("interrupt", "stop", "play"):
            await runner._control.handle(
                control_signal(mode, scope="room", room_id="ghost-room")
            )
        await drain_control_tasks(runner)
