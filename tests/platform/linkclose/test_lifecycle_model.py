"""Generated sequences of platform closes, control signals, supersedes and
host cancellations against a live BandLink: whatever the order, the session
keeps the invariants a host relies on."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import Phase, example, given, settings

from tests.platform.linkclose.lifecycle import (
    STEP_GUARD_S,
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
        await asyncio.wait_for(asyncio.shield(session.run), STEP_GUARD_S)
        assert server.connection_count == session.connections_at_end
    else:
        assert not session.run.done()

    await asyncio.wait_for(link.disconnect(), STEP_GUARD_S)
    await asyncio.wait_for(asyncio.shield(session.run), STEP_GUARD_S)

    assert session.hooks_running == 0
    assert session.applied == session.pushed[: len(session.applied)]

    connections = server.connection_count
    await asyncio.wait_for(link.connect(), STEP_GUARD_S)
    assert server.connection_count == connections + 1


@pytest.mark.usefixtures("millisecond_reconnects")
class TestLinkLifecycleModel:
    # No shrinking: every hung step of a shrink attempt waits out its guard,
    # which turns one regression into minutes of CI.
    @settings(
        max_examples=30,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=sequences)
    @example(steps=[Step.HELD_CONTROL, Step.CONTROL, Step.RELEASE_HELD_CONTROL])
    def test_every_sequence_keeps_the_session_invariants(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
