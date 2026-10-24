"""An INTERRUPT inside a recovery pass does not end it: the running room still serves everything queued."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    DRAINS,
    QUEUED,
    SINGLETONS,
    Call,
    Case,
    Drain,
    Model,
    Observed,
    Platform,
    all_processed,
    started_room,
)

pytestmark = pytest.mark.looptime


class TestInterruptDuringRecovery:
    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_a_read_it_overtakes_is_read_again(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        async with started_room(drain.case, platform=platform, model=model) as room:
            await room.quiesce()
            platform.post(*QUEUED)
            pending = platform.hold(drain.read)
            await room.recover()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiesce()

        assert room.observed() == Observed.settled(QUEUED, runs=drain.runs)

    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_the_startup_read_it_overtakes_is_read_again(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        platform.post(*QUEUED)
        pending = platform.hold(drain.read)
        async with started_room(drain.case, platform=platform, model=model) as room:
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiesce()

        assert room.observed() == Observed.settled(QUEUED, runs=drain.runs)

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_retried_terminal_write_does_not_end_the_pass(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            await room.quiesce()
            pending = await room.leave_write_unconfirmed()
            await room.recover()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiesce()

        assert room.observed().progress == (
            all_processed(QUEUED),
            [("m1",), ("m2",)],
        )

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_consumed_turn_leaves_the_queue_behind_it_due(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(case, platform=platform, model=model) as room:
            await room.quiesce()
            platform.post(*QUEUED)
            await room.recover()
            await room.until(model.entered.is_set)
            model.gate = False
            room.interrupt()
            await room.quiesce()

        assert room.observed() == Observed.settled(QUEUED, runs=[("m1",), ("m2",)])

    @pytest.mark.parametrize("drain", DRAINS, ids=str)
    async def test_an_original_it_defers_is_not_overtaken_by_a_later_arrival(
        self, drain: Drain, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            drain.case,
            platform=platform,
            model=model,
            enable_context_hydration=True,
        ) as room:
            await room.quiesce()
            platform.post("m1")
            pending = platform.hold(Call.context())
            await room.recover()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()
            await room.deliver(*platform.post("m2"))
            await room.recover()
            await room.quiesce()

        assert room.observed() == Observed.settled(QUEUED, runs=drain.runs)
