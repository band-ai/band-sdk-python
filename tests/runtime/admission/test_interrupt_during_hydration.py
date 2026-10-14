"""An INTERRUPT that lands while the room hydrates leaves its originals unmarked; the next pass runs each once."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    SHAPES,
    SINGLETONS,
    Call,
    Case,
    Endpoint,
    Model,
    Observed,
    Platform,
    processing_writes,
    started_room,
)

pytestmark = pytest.mark.looptime


class TestInterruptDuringHydration:
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_a_listed_original_is_marked_and_run_once(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, enable_context_hydration=True
        ) as room:
            await room.quiesce()
            pending = platform.hold(Call.context())
            await room.arrive()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()
            await room.recover()
            await room.quiesce()

        assert (room.observed(), platform.writes_to(Endpoint.PROCESSING)) == (
            Observed.settled(case.ids),
            processing_writes(case.ids),
        )

    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_a_websocket_only_original_is_kept_and_run_once(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, enable_context_hydration=True
        ) as room:
            await room.quiesce()
            pending = platform.hold(Call.context())
            await room.arrive(listed=False)
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()
            await room.recover()
            await room.quiesce()

        assert (room.observed(), platform.writes_to(Endpoint.PROCESSING)) == (
            Observed.settled(case.ids),
            processing_writes(case.ids),
        )
