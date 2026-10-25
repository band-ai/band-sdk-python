"""A control that settled, however it ended, no longer holds back the input behind it."""

from __future__ import annotations

import pytest

from tests.runtime.batching.kit import ControlScript, FakeRoom, started_room
from tests.runtime.helpers import runtime_failures

pytestmark = pytest.mark.looptime


class TestSettledControls:
    @pytest.mark.parametrize("batching", [True, False], ids=["batch", "off"])
    async def test_a_deferred_input_consumed_as_a_control_does_not_hold_back_new_input(
        self, platform: FakeRoom, batching: bool
    ) -> None:
        controls = ControlScript()

        async with started_room(platform, batching=batching, controls=controls) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("answer"))
            await room.until(lambda: room.turns.refused)
            # A pending question turns the refused input into its answer.
            controls.recognized.add("answer")
            room.turns.accepting = True
            await room.deliver(platform.post("ping"))
            await room.quiesce()

        assert (controls.handled, room.turns.ids) == (["answer"], [("ping",)])

    async def test_a_control_interrupted_while_handled_does_not_hold_back_new_input(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"}, hangs={"approve"})

        async with started_room(platform, controls=controls) as room:
            await room.deliver(platform.post("approve"))
            await room.until(lambda: controls.started)
            room.ctx.interrupt()
            await room.settled("approve")
            await room.deliver(platform.post("ping"))
            await room.quiesce()

        assert room.turns.ids == [("ping",)]

    async def test_a_control_interrupted_before_admission_recovers_before_later_input(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"})
        platform.interrupt_during_processing.add("approve")

        async with started_room(platform, controls=controls) as room:
            await room.deliver(platform.post("approve"))
            await room.admission_attempted("approve")
            await room.resync()
            await room.settled("approve")
            await room.deliver(platform.post("ping"))
            await room.quiesce()

        assert (
            controls.handled,
            platform.admission_attempts.count("approve"),
            room.turns.ids,
        ) == (["approve"], 2, [("ping",)])

    async def test_a_control_interrupted_while_handled_lets_the_input_queued_behind_it_run(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"approve"}, hangs={"approve"})

        async with started_room(platform, controls=controls) as room:
            await room.deliver(platform.post("approve"), platform.post("ping"))
            await room.until(lambda: controls.started)
            room.ctx.interrupt()
            await room.settled("approve")
            await room.quiesce()

        assert room.turns.ids == [("ping",)]

    async def test_a_control_whose_handler_fails_does_not_hold_back_new_input(
        self, platform: FakeRoom
    ) -> None:
        controls = ControlScript(recognized={"c1"}, raises={"c1"})

        async with started_room(platform, controls=controls) as room:
            await room.deliver(platform.post("c1"))
            await room.until(lambda: runtime_failures(room.ctx.link))
            await room.deliver(platform.post("ping"))
            await room.quiesce()

        assert (room.turns.ids, len(runtime_failures(room.ctx.link))) == (
            [("ping",)],
            1,
        )

    async def test_a_control_whose_handler_fails_lets_the_input_queued_behind_it_run(
        self, platform: FakeRoom
    ) -> None:
        platform.post("c1")
        platform.post("ping")
        controls = ControlScript(recognized={"c1"}, raises={"c1"})

        async with started_room(platform, controls=controls) as room:
            await room.quiesce()

        assert (room.turns.ids, len(runtime_failures(room.ctx.link))) == (
            [("ping",)],
            1,
        )
