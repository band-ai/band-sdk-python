"""Generated sequences of platform closes, control signals, supersedes and
host cancellations against a live BandLink: whatever the order, the session
keeps the invariants a host relies on."""

from __future__ import annotations

import asyncio

from hypothesis import Phase, example, given, settings

from band.platform.event import PlatformEvent, WebSocketDisconnectedEvent
from band.platform.link import BandLink
from tests.platform.linkclose.kit import drain_events
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
        # The run ended with the transport, so nothing reconnects after the
        # verdict, and it was reported exactly once.
        assert (server.connection_count, len(server.upgrades)) == (
            session.connections_at_end,
            session.upgrades_at_end,
        )
        assert terminal_events(link) == [
            WebSocketDisconnectedEvent(payload=link.last_disconnect_reason)
        ]
    else:
        assert not session.run.done()
        assert terminal_events(link) == []

    await asyncio.wait_for(link.disconnect(), STEP_GUARD_S)
    await asyncio.wait_for(asyncio.wait({session.run}), STEP_GUARD_S)

    assert session.hooks_running == 0
    assert session.applied.ids == session.pushed[: len(session.applied.ids)]

    connections = server.connection_count
    await asyncio.wait_for(link.connect(), STEP_GUARD_S)
    assert server.connection_count == connections + 1


def terminal_events(link: BandLink) -> list[PlatformEvent]:
    return [
        event
        for event in drain_events(link)
        if isinstance(event, WebSocketDisconnectedEvent)
    ]


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
    @example(steps=[Step.SERVER_POLICY_VIOLATION, Step.SUPERSEDE])
    @example(steps=[Step.REFUSED_RECONNECT, Step.SUPERSEDE, Step.HOST_RUNS_AGAIN])
    @example(steps=[Step.RETRIED_REFUSAL, Step.SUPERSEDE, Step.HOST_RUNS_AGAIN])
    def test_every_sequence_keeps_the_session_invariants(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
