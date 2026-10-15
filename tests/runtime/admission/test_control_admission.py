"""A control consumes its delivery only after processing is confirmed."""

from __future__ import annotations

import pytest

from band.client.streaming import ControlMode, DeliveryStatus
from tests.runtime.admission.barriers import (
    QUEUED,
    SINGLETONS,
    Call,
    Case,
    Endpoint,
    Model,
    Platform,
    started_room,
)
from tests.runtime.batching.kit import ControlScript

pytestmark = pytest.mark.looptime

HEAD, CONTROL = QUEUED


class TestControlAdmission:
    @pytest.mark.parametrize("case", SINGLETONS, ids=str)
    @pytest.mark.parametrize(
        "mode",
        [ControlMode.INTERRUPT, ControlMode.STOP, None],
        ids=["interrupt", "stop", "shutdown"],
    )
    async def test_a_control_before_processing_confirmation_consumes_nothing(
        self, case: Case, mode: ControlMode | None, platform: Platform, model: Model
    ) -> None:
        model.accepting = False
        controls = ControlScript(recognized={CONTROL})
        async with started_room(
            case, platform=platform, model=model, controls=controls
        ) as room:
            await room.deliver(*platform.post(HEAD))
            await room.quiesce()
            pending = platform.hold(Call.processing(CONTROL))
            await room.deliver(*platform.post(CONTROL))
            await room.reached(pending)
            if mode is None:
                await room.ctx.stop()
            else:
                await room.signal(mode)
            pending.release()
            await room.quiesce()

            assert (
                platform.statuses,
                controls.started,
                platform.writes_to(Endpoint.PROCESSED, Endpoint.FAILED),
            ) == (dict.fromkeys(QUEUED, DeliveryStatus.PROCESSING), [], [])
