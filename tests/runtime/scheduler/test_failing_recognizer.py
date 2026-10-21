"""A control recognizer that raises leaves the message ordinary input."""

from __future__ import annotations

import logging

import pytest

from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from tests.platform.settlement.kit import link_over
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import Waiting
from tests.runtime.delivery.kit import RestPeer, session_config
from tests.runtime.helpers import AGENT_ID, ROOM_ID, STOP_SECONDS, rest_client_over

pytestmark = pytest.mark.looptime


def failing_recognizer(ctx: ExecutionContext, event: MessageEvent) -> bool:
    raise RuntimeError("the adapter's control check failed")


async def never_handled(ctx: ExecutionContext, event: object) -> bool:
    return False


class TestFailingRecognizer:
    async def test_message_runs_as_ordinary_input_and_the_room_moves_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        peer = RestPeer()
        turns = Turns()
        wait = Waiting()
        async with rest_client_over(peer.answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                turns.single,
                config=session_config(batching=False),
                agent_id=AGENT_ID,
                on_message_control=never_handled,
                is_message_control=failing_recognizer,
            )
            await ctx.start()
            try:
                await wait.quiesce()
                await ctx.on_event(peer.post("first"))
                await ctx.on_event(peer.post("second"))
                await wait.until(lambda: len(turns.ids) == 2)
            finally:
                await ctx.stop(timeout=STOP_SECONDS)

        assert turns.ids == [("first",), ("second",)]
        # Recognition repeats every pass; each message alerts once.
        alerted = [
            record.args[1]
            for record in caplog.records
            if record.levelno == logging.ERROR and isinstance(record.args, tuple)
        ]
        assert alerted == ["first", "second"]
