"""A failed Claude start reports its own failure, whichever close of it
fails."""

from __future__ import annotations

import asyncio

from tests.adapters.lifecycle.kit import RetentionHost, raises_exactly
from tests.adapters.roompeer import write_instruction


class TestFailedStartClose:
    async def test_a_start_whose_first_close_fails_reports_its_own_failure(
        self, peer_host: RetentionHost
    ) -> None:
        """The SDK's own close of the failed start fails and the session's
        retry succeeds: the turn still reports the start failure."""
        host = peer_host
        host.fail_startup.touch()
        host.hold_startup.touch()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await host.start_reached("room-a")
        host.children[0].refused_closes = 1
        host.hold_startup.unlink()

        with raises_exactly(host.failed_start):
            await opening
        assert host.exited == [True]
