"""Late physical recovery redelivers refused input without periodic resync."""

from __future__ import annotations

import pytest

from band.core.turn_lifecycle import BackendTurns
from band.platform.link import BandLink
from tests.adapters.hold import Hold
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import IMPATIENT_TRANSITION_S, ResidenceHost
from tests.runtime.conftest import Waiting, wait_for_condition
from tests.runtime.deferral.kit import RecoveringAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    PostingPlatform,
    no_periodic_wake_config,
    rest_client_over,
    run_through,
    running_execution,
)


@pytest.mark.looptime
class TestRecoveryReadiness:
    async def test_late_release_wakes_the_refused_input(self) -> None:
        async with ResidenceHost(
            timeout=None, transition_timeout=IMPATIENT_TRANSITION_S
        ) as host:
            resident = host.room(ROOM_ID)
            await resident.turn()
            resident.closing = Hold()
            turns = BackendTurns(RecordingCancel(), provider="test")
            turns.recovering(
                ROOM_ID, object(), resident.room, generation=resident.room.generation
            )
            await resident.closing.arrived()
            peer = PostingPlatform()
            adapter = RecoveringAdapter(turns)
            async with rest_client_over(peer.answer) as rest:
                link = BandLink(agent_id=AGENT_ID, api_key="test-key")
                link.rest = rest
                ctx = run_through(adapter, link, config=no_periodic_wake_config())
                async with running_execution(ctx):
                    peer.add_message("A")
                    await ctx.on_event(peer.event("A"))
                    await adapter.refused.wait()
                    await Waiting.suspended(ctx)
                    assert adapter.completed == []
                    assert resident.live
                    resident.closing.released.set()
                    await resident.room.wait_released(
                        generation=resident.room.generation
                    )
                    await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                    assert adapter.completed == ["A"]
                    assert peer.marked("failed") == []
                    assert not resident.live
