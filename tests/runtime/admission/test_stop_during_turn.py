"""A stop during a turn leaves its originals recoverable; play runs them once more."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    REPORT,
    SHAPES,
    Case,
    Model,
    Observed,
    Platform,
    Stop,
    started_room,
)

# The shortest keep-alive SessionConfig allows: the heartbeat runs on the real
# clock because its timer hung under the virtual one on Windows.
HEARTBEAT = REPORT.settings | {
    "working_keep_alive_seconds": 1.5,
    "working_request_timeout_seconds": 1,
}


class TestStopDuringTurn:
    @pytest.mark.parametrize("stop", list(Stop), ids=str)
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_a_stop_before_the_model_is_invoked_runs_nothing(
        self, case: Case, stop: Stop, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, **REPORT.settings
        ) as room:
            pending = platform.hold(REPORT.call, answer=stop.answer)
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
            Observed.recoverable(case.ids, processing=case.ids),
            Observed.settled(case.ids),
        )

    @pytest.mark.parametrize("stop", [Stop.NATIVE, Stop.SHUTDOWN], ids=str)
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_a_stop_while_the_model_runs_aborts_it_and_play_runs_it_again(
        self, case: Case, stop: Stop, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(case, platform=platform, model=model) as room:
            await room.arrive()
            await room.until(model.entered.is_set)
            await stop.deliver(room)
            await room.quiet()
            stopped = room.observed()

            model.gate = False
            await stop.resume(room)
            await room.settled()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids, processing=case.ids, runs=[case.ids]),
            Observed.settled(case.ids, runs=[case.ids, case.ids]),
        )

    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_a_platform_stop_seen_by_the_heartbeat_aborts_the_model(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(
            case, platform=platform, model=model, **HEARTBEAT
        ) as room:
            await room.arrive()
            await room.until(model.entered.is_set)
            await Stop.PLATFORM.deliver(room)
            await room.quiet()
            stopped = room.observed()

            model.gate = False
            await Stop.PLATFORM.resume(room)
            await room.settled()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids, processing=case.ids, runs=[case.ids]),
            Observed.settled(case.ids, runs=[case.ids, case.ids]),
        )
