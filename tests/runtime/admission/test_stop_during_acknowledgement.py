"""A stop before the first terminal write neither replays the model nor changes the outcome."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    SHAPES,
    Call,
    Case,
    Endpoint,
    Model,
    Platform,
    Stop,
    all_processed,
    started_room,
)


class TestStopDuringAcknowledgement:
    @pytest.mark.parametrize("stop", list(Stop), ids=str)
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_the_finished_turn_is_settled_after_the_stop_without_a_second_run(
        self, case: Case, stop: Stop, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            pending = platform.hold(Call.processed(case.target), answer=stop.answer)
            await room.arrive()
            await room.reached(pending)
            await stop.deliver(room)
            pending.release()
            await room.quiet()

            await stop.resume(room)
            await room.settled()
            resumed = room.observed()

        assert (
            resumed.statuses,
            resumed.runs,
            platform.writes_to(Endpoint.FAILED),
        ) == (
            all_processed(case.ids),
            [case.ids],
            [],
        )

    @pytest.mark.parametrize("stop", list(Stop), ids=str)
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_an_interrupts_outcome_survives_a_later_stop(
        self, case: Case, stop: Stop, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(case, platform=platform, model=model) as room:
            pending = platform.hold(Call.processed(case.target), answer=stop.answer)
            await room.arrive()
            await room.until(model.entered.is_set)
            room.interrupt()
            await room.reached(pending)
            await stop.deliver(room)
            pending.release()
            await room.quiet()

            await stop.resume(room)
            await room.settled()

        assert room.observed().statuses == all_processed(case.ids)
        assert platform.writes_to(Endpoint.FAILED) == []
