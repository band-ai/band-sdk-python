"""Durable batch failure also excludes a delivery from control recovery."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.admission.barriers import (
    ON,
    SINGLETONS,
    Case,
    Model,
    Platform,
    started_room,
)
from tests.runtime.batching.kit import ControlScript

pytestmark = pytest.mark.looptime

FAILED = "failed"


class TestFailedControlRecovery:
    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    async def test_restart_preserves_failure_unless_singleton_retries_are_enabled(
        self, case: Case, platform: Platform, journal: list[str]
    ) -> None:
        model = Model(journal=journal, fail_on={FAILED})
        async with started_room(ON, platform=platform, model=model) as room:
            await room.deliver(*platform.post(FAILED))
            await room.quiesce()
        assert platform.statuses == {FAILED: DeliveryStatus.FAILED}

        controls = ControlScript(recognized={FAILED})
        model = Model(journal=journal, accepting=False)
        async with started_room(
            case, platform=platform, model=model, controls=controls
        ) as room:
            await room.deliver(*platform.post("head"))
            await room.quiesce()
            await room.recover()
            await room.quiesce()

        assert (platform.statuses[FAILED], controls.handled) == (
            (DeliveryStatus.FAILED, [])
            if case.batching
            else (DeliveryStatus.PROCESSED, [FAILED])
        )
