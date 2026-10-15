"""A control that lands while an admitted control is hydrating decides whether it is consumed."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    QUEUED,
    REFRESH,
    SINGLETONS,
    Case,
    Model,
    Observed,
    Platform,
    all_processed,
    processed_writes,
    started_room,
)
from tests.runtime.batching.kit import ControlScript

pytestmark = pytest.mark.looptime

HEAD, CONTROL = QUEUED


class TestControlInterruptedDuringHydration:
    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_an_interrupt_consumes_the_control_without_running_its_handler(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        controls = ControlScript(recognized={CONTROL})
        async with started_room(
            case,
            platform=platform,
            model=model,
            controls=controls,
            warm=REFRESH.warm,
            **REFRESH.settings,
        ) as room:
            pending = await room.hold_control_hydration()
            room.interrupt()
            pending.release()
            await room.quiesce()
            interrupted = (room.observed().statuses, list(controls.started))

            model.accepting = True
            await room.recover()
            await room.quiesce()

        assert (interrupted, room.observed()) == (
            ({HEAD: "processing", CONTROL: "processed"}, []),
            Observed(
                statuses=all_processed(QUEUED),
                runs=[(HEAD,)],
                terminal_writes=processed_writes((CONTROL, HEAD)),
            ),
        )

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_stop_leaves_the_control_for_play(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        controls = ControlScript(recognized={CONTROL})
        async with started_room(
            case,
            platform=platform,
            model=model,
            controls=controls,
            warm=REFRESH.warm,
            **REFRESH.settings,
        ) as room:
            pending = await room.hold_control_hydration()
            room.ctx.stop_room()
            pending.release()
            await room.quiesce()
            stopped = (room.observed().statuses, list(controls.started))

            model.accepting = True
            await room.ctx.resume_room()
            await room.quiesce()

        assert (stopped, room.observed().statuses) == (
            ({HEAD: "processing", CONTROL: "processing"}, []),
            all_processed(QUEUED),
        )
