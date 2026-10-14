"""A STOP on agent.control leaves a WebSocket-only original recoverable; PLAY runs it again."""

from __future__ import annotations

import pytest

from band.client.streaming import ControlMode
from tests.runtime.admission.barriers import (
    SINGLETONS,
    WINDOWS,
    Case,
    Model,
    Observed,
    Platform,
    Window,
    started_room,
)

pytestmark = pytest.mark.looptime


class TestWebSocketOnlyStop:
    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_stop_while_the_model_runs_keeps_the_original_for_play(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(case, platform=platform, model=model) as room:
            await room.arrive(listed=False)
            await room.until(model.entered.is_set)
            platform.stop()
            await room.signal(ControlMode.STOP)
            await room.quiet()
            stopped = room.observed()

            model.gate = False
            platform.play()
            await room.signal(ControlMode.PLAY)
            await room.quiesce()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids, processing=case.ids, runs=[case.ids]),
            Observed.settled(case.ids, runs=[case.ids, case.ids]),
        )

    @pytest.mark.parametrize("window", WINDOWS, ids=str)
    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_stop_before_the_model_is_invoked_keeps_the_original_for_play(
        self, case: Case, window: Window, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, warm=window.warm, **window.settings
        ) as room:
            pending = platform.hold(window.call)
            await room.arrive(listed=False)
            await room.reached(pending)
            platform.stop()
            await room.signal(ControlMode.STOP)
            pending.release()
            await room.quiet()
            stopped = room.observed()

            platform.play()
            await room.signal(ControlMode.PLAY)
            await room.quiesce()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids, processing=case.ids),
            Observed.settled(case.ids),
        )

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_an_original_arriving_while_stopped_waits_for_play(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            platform.stop()
            await room.signal(ControlMode.STOP)
            await room.arrive(listed=False)
            await room.quiesce()
            stopped = room.observed()

            platform.play()
            await room.signal(ControlMode.PLAY)
            await room.quiesce()
            resumed = room.observed()

        assert (stopped, resumed) == (
            Observed.recoverable(case.ids),
            Observed.settled(case.ids),
        )
