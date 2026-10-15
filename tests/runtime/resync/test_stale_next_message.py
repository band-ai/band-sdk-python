"""A /next message the platform already reports processed is never run again."""

from __future__ import annotations

from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import AGENT_ID
from tests.runtime.resync.kit import ReconciledRoom

PROCESSED_BY_AGENT = {"delivery_status": {AGENT_ID: {"status": "processed"}}}


class TestStaleNextMessage:
    async def test_it_is_neither_run_nor_claimed(
        self, reconciled_room: ReconciledRoom
    ) -> None:
        ctx, peer, invoked = reconciled_room
        peer.add_message("stale", metadata=PROCESSED_BY_AGENT)

        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        assert (invoked, peer.requested_marks) == ([], [])
