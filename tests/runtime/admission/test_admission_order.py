"""Every selected original is admitted before the model runs or any write settles."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import (
    SHAPES,
    Case,
    Model,
    Platform,
    processed_writes,
    processing_writes,
    run,
    started_room,
)


class TestAdmissionOrder:
    @pytest.mark.parametrize("case", SHAPES, ids=str)
    async def test_the_model_runs_only_after_the_last_original_is_admitted(
        self, case: Case, platform: Platform, model: Model, journal: list[str]
    ) -> None:
        async with started_room(case, platform=platform, model=model) as room:
            await room.arrive()
            await room.settled()

        assert journal == [
            *processing_writes(case.ids),
            run(case.ids),
            *processed_writes(case.ids),
        ]
