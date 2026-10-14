"""A recognized control is handled on its own; ordinary work is never probed as one."""

from __future__ import annotations

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import ControlScript, FakeRoom, started_room


class TestControlBoundaries:
    async def test_a_control_ends_the_turn_before_it_and_runs_alone(
        self, platform: FakeRoom
    ) -> None:
        for message_id in ("m1", "approve", "m3"):
            platform.post(message_id)
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            await room.settled("m1", "approve", "m3")

        assert (room.turns.ids, controls.handled) == (
            [("m1",), ("m3",)],
            ["approve"],
        )

    async def test_a_control_the_rest_list_omits_is_handled_behind_deferred_work(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.admission_attempted("m1")
            await room.deliver(platform.post("approve", in_rest=False))
            await room.settled("approve")

        assert (controls.handled, platform.status_of("m1")) == (
            ["approve"],
            DeliveryStatus.PROCESSING,
        )

    async def test_probing_an_ordinary_candidate_for_a_control_writes_nothing(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})

        async with started_room(platform, controls=controls) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.admission_attempted("m1")
            attempted = list(platform.admission_attempts)
            await room.deliver(platform.post("m2"))
            await room.deliver(platform.post("m3"))
            await room.until(lambda: platform.page_reads > 2)

        assert platform.admission_attempts == attempted

    async def test_each_control_is_classified_after_the_one_before_it_ran(
        self, platform: FakeRoom
    ) -> None:
        platform.post("answer-1")
        platform.post("answer-2")
        controls = ControlScript(recognized={"answer-1", "answer-2"})
        # Consuming the first answer closes the pending question, so the
        # second free-text message is an ordinary input again.
        controls.on_handled = lambda message_id: controls.recognized.discard("answer-2")

        async with started_room(platform, controls=controls) as room:
            await room.settled("answer-1", "answer-2")

        assert (controls.handled, room.turns.ids) == (["answer-1"], [("answer-2",)])

    async def test_a_recognized_message_the_handler_declines_runs_as_ordinary_input(
        self, platform: FakeRoom
    ) -> None:
        platform.post("expired")
        controls = ControlScript(recognized={"expired"}, declines={"expired"})

        async with started_room(platform, controls=controls) as room:
            await room.settled("expired")

        assert (controls.handled, room.turns.ids) == ([], [("expired",)])
