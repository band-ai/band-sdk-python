"""A room that would need more steps than one signal allows stops loudly."""

from __future__ import annotations

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.runtime import rooms as rooms_module
from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import RoomCleanup, StartGate
from tests.runtime.rooms.kit import Phase, phase, settle


class TestCircuitBreaker:
    @pytest.fixture(autouse=True)
    def one_step_per_signal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Correct transitions never exceed the budget, so it is narrowed to
        # make a retire-then-start chain trip it.
        monkeypatch.setattr(rooms_module, "MAX_STEPS_PER_SIGNAL", 1)

    async def test_the_breaker_fails_waiters_and_the_next_signal_resumes(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        start_gate.failures = 1
        with pytest.raises(RuntimeError, match="execution start failed"):
            await rooms.admit("room-1")

        rooms.converge("room-1")
        pending = rooms.pending("room-1")
        assert pending is not None
        with pytest.raises(CleanupIncompleteError, match="steps without a new signal"):
            await pending
        assert room_cleanup.rooms == ["room-1"]
        assert phase(rooms, "room-1") == Phase.PENDING

        rooms.converge("room-1")
        await settle(rooms, "room-1")
        assert phase(rooms, "room-1") == Phase.SERVING
        assert len(start_gate.built) == 2
