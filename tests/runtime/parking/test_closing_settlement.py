"""A terminal write the platform accepts while the room closes is not owed again."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.platform.settlement.kit import MISSING_ATTEMPT, ROOM_ID, link_over
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import Waiting
from tests.runtime.helpers import AGENT_ID
from tests.runtime.parking.harness import RoomPeer

CONFIG = SessionConfig(
    max_message_retries=3,
    enable_context_hydration=False,
    enable_working_state=False,
    report_turn_failures_to_room=False,
)


@pytest.mark.looptime
class TestClosingSettlement:
    async def test_failure_settled_during_shutdown_is_retried_after_rejoin(
        self,
    ) -> None:
        peer = RoomPeer()
        wait = Waiting()
        turns = Turns(gate=True, fail_on={"m1"})
        async with peer.client() as rest:
            link = link_over(rest)
            runtime = AgentRuntime(link, AGENT_ID, turns.single, session_config=CONFIG)
            await runtime.presence.on_room_joined(ROOM_ID, {})
            await wait.until(lambda: ROOM_ID in runtime.executions)
            first = runtime.executions[ROOM_ID]
            assert isinstance(first, ExecutionContext)
            await runtime.presence.on_room_event(ROOM_ID, peer.post("m1"))
            await wait.until(lambda: turns.ids == [("m1",)])

            # A graceful stop lets the running turn fail and settle inside its grace.
            stopping = asyncio.create_task(runtime.stop(timeout=30))
            await wait.until(lambda: first.is_closing)
            turns.release.set()
            await stopping
            # The platform closed that attempt: a repeated write finds none open.
            peer.script(MISSING_ATTEMPT, "m1")

            turns.gate = False
            rejoined = ExecutionContext(
                ROOM_ID,
                link,
                turns.single,
                config=CONFIG,
                agent_id=AGENT_ID,
                claim_registry=first.claims,
            )
            await rejoined.start()
            try:
                await wait.until(lambda: len(turns.ids) == 2)
            finally:
                await rejoined.stop(timeout=1)

        assert (turns.ids, peer.writes[:3]) == (
            [("m1",), ("m1",)],
            ["m1/processing", "m1/failed", "m1/processing"],
        )
