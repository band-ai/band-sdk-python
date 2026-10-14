"""An INTERRUPT after full admission consumes every original, even before the model runs."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    SHAPES,
    WINDOWS,
    Case,
    Model,
    Observed,
    Platform,
    Window,
    started_room,
)


class TestInterruptAfterAdmission:
    @pytest.mark.parametrize("window", WINDOWS, ids=str)
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_before_the_model_is_invoked_every_original_is_processed(
        self, case: Case, window: Window, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, warm=window.warm, **window.settings
        ) as room:
            pending = platform.hold(window.call)
            await room.arrive()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()

        assert room.observed() == Observed.settled(case.ids, runs=[])

    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_while_the_model_runs_every_original_is_processed(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        model.gate = True
        async with started_room(case, platform=platform, model=model) as room:
            await room.arrive()
            await room.until(model.entered.is_set)
            room.interrupt()
            await room.quiet()

        assert room.observed() == Observed.settled(case.ids)
