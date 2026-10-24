"""One refused, stopped or unanswered admission defers the whole selection."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    POSITIONS,
    Answer,
    Call,
    Case,
    Model,
    Observed,
    Platform,
    started_room,
)


class TestAdmissionRefusal:
    @pytest.mark.parametrize(
        "answer", [Answer.REFUSE, Answer.STOP, Answer.LOST], ids=str
    )
    @pytest.mark.parametrize("case", POSITIONS, ids=str)
    async def test_nothing_runs_or_settles_and_recovery_runs_every_original_once(
        self, case: Case, answer: Answer, platform: Platform, model: Model
    ) -> None:
        target = Call.processing(case.target)
        async with started_room(case, platform=platform, model=model) as room:
            platform.script(target, answer=answer)
            await room.arrive()
            await room.called(target)
            await room.quiet()
            deferred = room.observed()

            platform.script(target, answer=Answer.ACCEPT)
            await room.recover()
            await room.settled()
            recovered = room.observed()

        assert (deferred, recovered) == (
            Observed.recoverable(case.ids, processing=answer.recorded(case)),
            Observed.settled(case.ids),
        )
