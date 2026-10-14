"""A stop at a pending admission write starts nothing; play runs the originals once."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    POSITIONS,
    Call,
    Case,
    Model,
    Observed,
    Platform,
    Stop,
    started_room,
)


class TestStopDuringAdmission:
    @pytest.mark.parametrize("stop", list(Stop), ids=str)
    @pytest.mark.parametrize("case", POSITIONS, ids=str)
    async def test_nothing_runs_and_resuming_runs_every_original_once(
        self, case: Case, stop: Stop, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            pending = platform.hold(Call.processing(case.target), answer=stop.answer)
            await room.arrive()
            await room.reached(pending)
            await stop.deliver(room)
            pending.release()
            await room.quiet()
            stopped = room.observed()

            await stop.resume(room)
            await room.settled()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids, processing=stop.answer.recorded(case)),
            Observed.settled(case.ids),
        )
