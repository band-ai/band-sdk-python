"""An INTERRUPT before every original is confirmed admitted consumes none of them."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    POSITIONS,
    SHAPES,
    Answer,
    Call,
    Case,
    Model,
    Observed,
    Platform,
    started_room,
)


class TestInterruptBeforeAdmission:
    @pytest.mark.parametrize(
        "answer", [Answer.ACCEPT, Answer.REFUSE, Answer.LOST], ids=str
    )
    @pytest.mark.parametrize("case", POSITIONS, ids=str)
    async def test_a_pending_processing_write_cannot_revive_the_interrupted_scope(
        self, case: Case, answer: Answer, platform: Platform, model: Model
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            pending = platform.hold(Call.processing(case.target), answer=answer)
            await room.arrive()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()
            interrupted = room.observed()

            await room.recover()
            await room.settled()
            recovered = room.observed()

        assert (interrupted, recovered) == (
            Observed.recoverable(case.ids, processing=answer.recorded(case)),
            Observed.settled(case.ids),
        )

    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_an_interrupt_while_the_room_hydrates_leaves_every_original_untouched(
        self, case: Case, platform: Platform, model: Model
    ) -> None:
        async with started_room(
            case, platform=platform, model=model, enable_context_hydration=True
        ) as room:
            pending = platform.hold(Call.context())
            await room.arrive()
            await room.reached(pending)
            room.interrupt()
            pending.release()
            await room.quiet()
            interrupted = room.observed()

            await room.recover()
            await room.settled()
            recovered = room.observed()

        assert (interrupted, recovered) == (
            Observed.recoverable(case.ids),
            Observed.settled(case.ids),
        )
