"""A STOP inside a recovery pass ends it, even while the platform still lists the work; PLAY serves it once."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    DRAINS,
    QUEUED,
    SINGLETONS,
    Case,
    Drain,
    Model,
    Observed,
    Platform,
    all_processed,
    started_room,
)

pytestmark = pytest.mark.looptime


class TestStopDuringRecovery:
    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_a_read_it_overtakes_runs_nothing_until_play(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        async with started_room(drain.case, platform=platform, model=model) as room:
            await room.quiesce()
            platform.post(*QUEUED)
            pending = platform.hold(drain.read)
            await room.recover()
            await room.reached(pending)
            room.ctx.stop_room()
            pending.release()
            await room.quiesce()
            stopped = room.observed()

            await room.ctx.resume_room()
            await room.quiesce()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(QUEUED),
            Observed.settled(QUEUED, runs=drain.runs),
        )

    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_the_startup_read_it_overtakes_runs_nothing_until_play(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        platform.post(*QUEUED)
        pending = platform.hold(drain.read)
        async with started_room(drain.case, platform=platform, model=model) as room:
            await room.reached(pending)
            room.ctx.stop_room()
            pending.release()
            await room.quiesce()
            stopped = room.observed()

            await room.ctx.resume_room()
            await room.quiesce()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(QUEUED),
            Observed.settled(QUEUED, runs=drain.runs),
        )

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_retried_terminal_write_runs_nothing_until_play(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            await room.quiesce()
            pending = await room.leave_write_unconfirmed()
            await room.recover()
            await room.reached(pending)
            room.ctx.stop_room()
            pending.release()
            await room.quiesce()
            stopped = room.observed()

            await room.ctx.resume_room()
            await room.quiesce()
            resumed = room.observed()

        assert (stopped.progress, resumed.progress) == (
            ({"m1": "processed", "m2": None}, [("m1",)]),
            (all_processed(QUEUED), [("m1",), ("m2",)]),
        )

    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_a_play_that_follows_serves_each_original_once(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        async with started_room(drain.case, platform=platform, model=model) as room:
            await room.quiesce()
            platform.post(*QUEUED)
            pending = platform.hold(drain.read)
            await room.recover()
            await room.reached(pending)
            room.ctx.stop_room()
            await room.ctx.resume_room()
            pending.release()
            await room.quiesce()

        assert room.observed() == Observed.settled(QUEUED, runs=drain.runs)
