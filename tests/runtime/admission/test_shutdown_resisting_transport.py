"""A shutdown wins over an admission write whose transport resists cancellation."""

from __future__ import annotations

import pytest

from band.core.exceptions import CleanupIncompleteError
from tests.runtime.admission.barriers import (
    POSITIONS,
    Answer,
    Call,
    Case,
    Model,
    Observed,
    Platform,
    started_room,
)


class TestShutdownResistingTransport:
    @pytest.mark.parametrize("case", POSITIONS, ids=str)
    async def test_a_late_response_cannot_start_the_turn_of_a_stopped_room(
        self,
        case: Case,
        platform: Platform,
        model: Model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        async with started_room(case, platform=platform, model=model) as room:
            pending = platform.hold(Call.processing(case.target), suppress_cancel=True)
            await room.arrive()
            await room.reached(pending)
            with pytest.raises(CleanupIncompleteError):
                await room.ctx.stop()
            pending.release()
            await room.quiet()

        assert room.observed() == Observed.recoverable(
            case.ids, processing=Answer.ACCEPT.recorded(case)
        )
