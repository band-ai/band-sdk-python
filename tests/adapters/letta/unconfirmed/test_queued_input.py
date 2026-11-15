"""Input queued behind an unconfirmed Letta run fails terminally, never replayed."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from tests.adapters.letta.unconfirmed.kit import (
    MODES,
    StreamingMode,
    adapter_over_recording_server,
)
from tests.adapters.lettakit import LettaStream, make_assistant_message
from tests.runtime.conftest import wait_for_condition
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
class TestQueuedInput:
    @pytest.mark.parametrize("mode", MODES)
    async def test_queued_and_new_input_fail_terminally_without_replay(
        self, mode: StreamingMode
    ) -> None:
        adapter, client = adapter_over_recording_server(mode, room_id=ROOM_ID)
        # The first run is held in flight, then the server ends it unconfirmed.
        client.hold()
        client.script_turns(LettaStream([make_assistant_message("partial")]))
        peer = PostingPlatform()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await wait_for_condition(lambda: bool(client.sent))
                peer.add_message("B")
                await ctx.on_event(peer.event("B"))
                client.release.set()
                await wait_for_condition(lambda: peer.marked("failed") == ["A", "B"])
                peer.add_message("C")
                await ctx.on_event(peer.event("C"))
                await wait_for_condition(
                    lambda: peer.marked("failed") == ["A", "B", "C"]
                )

        assert client.journal == [mode.sent, mode.cancel]
        assert peer.marked("processed") == []
        assert adapter.backend_turns.unavailable(ROOM_ID)
