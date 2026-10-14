"""A control that cannot be attempted yet keeps its place and is retried, never run as input."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import ControlScript, FakeRoom, started_room
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime


class TestDeferredControls:
    async def test_a_control_claimed_elsewhere_waits_for_the_claim_then_is_handled(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.admission_attempted("m1")
            room.ctx.claims.try_claim(ROOM_ID, "approve")
            await room.deliver(platform.post("approve", in_rest=False))
            await room.quiesce()
            while_claimed = (list(controls.handled), list(platform.admission_attempts))

            room.ctx.claims.release(ROOM_ID, "approve")
            await room.resync()
            await room.settled("approve")

        assert while_claimed == ([], ["m1"])
        assert (controls.handled, room.turns.ids) == (["approve"], [])

    async def test_a_control_whose_processing_write_is_refused_is_retried_once_admitted(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.admission_attempted("m1")
            platform.refuse_processing.add("approve")
            await room.deliver(platform.post("approve", in_rest=False))
            await room.admission_attempted("approve")
            await room.quiesce()
            while_refused = (list(controls.handled), platform.status_of("approve"))

            await room.readmit()
            await room.settled("approve")

        assert while_refused == ([], None)
        assert (controls.handled, room.turns.ids) == (["approve"], [])

    async def test_a_control_at_the_head_claimed_elsewhere_holds_back_the_input_after_it(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            room.ctx.claims.try_claim(ROOM_ID, "approve")
            await room.deliver(platform.post("approve"), platform.post("m2"))
            await room.quiesce()
            while_claimed = (list(controls.handled), list(room.turns.ids))

            room.ctx.claims.release(ROOM_ID, "approve")
            await room.resync()
            await room.settled("approve", "m2")

        assert while_claimed == ([], [])
        assert (controls.handled, room.turns.ids) == (["approve"], [("m2",)])

    async def test_a_control_at_the_head_that_cannot_start_holds_back_the_input_after_it(
        self, platform: FakeRoom
    ) -> None:
        platform.post("approve")
        platform.post("m2")
        platform.refuse_processing.add("approve")
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            await room.admission_attempted("approve")
            await room.quiesce()
            while_refused = (
                list(controls.handled),
                list(room.turns.ids),
                platform.status_of("m2"),
            )

            await room.readmit()
            await room.settled("approve", "m2")

        assert while_refused == ([], [], None)
        assert (controls.handled, room.turns.ids) == (["approve"], [("m2",)])
        assert platform.status_of("approve") == DeliveryStatus.PROCESSED

    async def test_a_control_refused_once_at_the_head_is_never_run_as_input(
        self, platform: FakeRoom
    ) -> None:
        platform.post("approve")
        platform.refuse_processing_once.add("approve")
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            await room.settled("approve")
            await room.quiesce()

        assert (controls.handled, room.turns.ids) == (["approve"], [])

    async def test_a_deferred_input_that_a_handled_control_turned_into_a_control_is_consumed(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})
        # Handling the approval opens the question the refused input answers.
        controls.on_handled = lambda message_id: controls.recognized.add("answer")

        async with started_room(platform, controls=controls) as room:
            room.turns.accepting = False
            # The list omits it, as it does a thread reply, so only the room's
            # retained original can be offered to control handling again.
            await room.deliver(platform.post("answer", in_rest=False))
            await room.until(lambda: room.turns.refused)
            room.turns.accepting = True
            await room.deliver(platform.post("approve"))
            await room.deliver(platform.post("ping"))
            await room.quiesce()

        assert (controls.handled, room.turns.ids) == (
            ["approve", "answer"],
            [("ping",)],
        )
