"""Late physical recovery admits fresh input without replaying abandoned input."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.turn_lifecycle import BackendTurns
from band.platform.link import BandLink
from tests.adapters.hold import Hold
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import IMPATIENT_TRANSITION_S, ResidenceHost
from tests.runtime.conftest import Waiting
from tests.runtime.deferral.kit import RecoveringAdapter
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    no_periodic_wake_config,
    rest_client_over,
    run_through,
    running_execution,
)


@pytest.mark.looptime
class TestRecoveryReadiness:
    async def test_late_release_admits_fresh_input_after_terminal_unavailability(
        self,
    ) -> None:
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
            peer = RestPeer()
            adapter = RecoveringAdapter(turns)
            async with resident.closing, rest_client_over(peer.answer) as rest:
                link = BandLink(agent_id=AGENT_ID, api_key="test-key")
                link.rest = rest
                ctx = run_through(adapter, link, config=no_periodic_wake_config())
                async with running_execution(ctx):
                    await ctx.on_event(peer.post("A"))
                    await peer.write_received(DeliveryStatus.FAILED, "A")
                    assert adapter.completed == []
                    assert resident.live
                    resident.closing.released.set()
                    await resident.room.wait_released(
                        generation=resident.room.generation
                    )
                    await ctx.on_event(peer.post("B"))
                    await peer.write_received(DeliveryStatus.PROCESSED, "B")
                    await Waiting.suspended(ctx)
                    assert adapter.completed == ["B"]
                    assert peer.status_of("A") is DeliveryStatus.FAILED
                    assert not resident.live
