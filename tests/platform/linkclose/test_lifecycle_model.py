"""Generated sequences of platform closes, control signals, supersedes and
host cancellations against a live BandLink: whatever the order, the session
keeps the invariants a host relies on."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import suppress

import pytest
from hypothesis import Phase, example, given, settings

from band.core.exceptions import AgentDisconnectedError
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


def await_run(session: LinkSession) -> Awaitable[None]:
    """The run task's end, whether it returned or surfaced its terminal cause."""
    return asyncio.wait_for(asyncio.shield(session.run), STEP_GUARD_S)


async def assert_invariants(session: LinkSession) -> None:
    link, server = session.link, session.server
    if session.ended:
        # A run that ended — by return or by a typed terminal disconnect —
        # means the transport is shut down for good, so the connection count
        # below can never grow afterwards.
        with suppress(AgentDisconnectedError):
            await await_run(session)
        assert server.connection_count == session.connections_at_end
    else:
        assert not session.run.done()

    await asyncio.wait_for(link.disconnect(), STEP_GUARD_S)
    with suppress(AgentDisconnectedError):
        await await_run(session)

    assert session.hooks_running == 0
    assert session.applied.ids == session.pushed[: len(session.applied.ids)]

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
