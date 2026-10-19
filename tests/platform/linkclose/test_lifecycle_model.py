"""Generated sequences of platform closes, control signals, supersedes and
host cancellations against a live BandLink: whatever the order, the session
keeps the invariants a host relies on."""

from __future__ import annotations

import asyncio

from hypothesis import given, settings

from tests.platform.linkclose.kit import HANG_GUARD_S
from tests.platform.linkclose.lifecycle import (
    LinkSession,
    Step,
    link_session,
    sequences,
)


async def play(steps: list[Step]) -> None:
    async with link_session() as session:
        for step in steps:
            await session.take(step)
        await assert_invariants(session)


async def assert_invariants(session: LinkSession) -> None:
    link, server = session.link, session.server
    if session.ended:
        # run_forever returning means the transport is shut down for good,
        # so the connection count below can never grow afterwards.
        await asyncio.wait_for(asyncio.shield(session.run), HANG_GUARD_S)
        assert server.connection_count == session.connections_at_end
    else:
        assert not session.run.done()

    await asyncio.wait_for(link.disconnect(), HANG_GUARD_S)
    await asyncio.wait_for(asyncio.shield(session.run), HANG_GUARD_S)

    assert session.hooks_running == 0
    assert session.applied == [c for c in session.pushed if c in session.applied]

    connections = server.connection_count
    await asyncio.wait_for(link.connect(), HANG_GUARD_S)
    assert link.is_connected
    assert server.connection_count == connections + 1


class TestLinkLifecycleModel:
    @settings(max_examples=30, deadline=None)
    @given(steps=sequences())
    def test_every_sequence_keeps_the_session_invariants(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
