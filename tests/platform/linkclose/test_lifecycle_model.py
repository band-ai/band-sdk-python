"""Generated sequences of platform closes, control signals, supersedes and
host cancellations against a live BandLink: whatever the order, the session
keeps the invariants a host relies on."""

from __future__ import annotations

import asyncio

from hypothesis import Phase, example, given, settings

from tests.platform.linkclose.lifecycle import (
    Step,
    play,
    sequences,
)


class TestLinkLifecycleModel:
    # No shrinking: every hung step of a shrink attempt waits out its guard,
    # which turns one regression into minutes of CI.
    @settings(
        max_examples=30,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=sequences)
    @example(steps=[Step.ROOM_SUBSCRIBE, Step.ROOM_UNSUBSCRIBE, Step.ROOM_SUBSCRIBE])
    @example(steps=[Step.ROOM_SUBSCRIBE, Step.NETWORK_DROP, Step.ROOM_UNSUBSCRIBE])
    @example(steps=[Step.HELD_CONTROL, Step.CONTROL, Step.RELEASE_HELD_CONTROL])
    @example(steps=[Step.SERVER_POLICY_VIOLATION, Step.SUPERSEDE])
    @example(steps=[Step.REFUSED_RECONNECT, Step.SUPERSEDE, Step.HOST_RUNS_AGAIN])
    @example(steps=[Step.RETRIED_REFUSAL, Step.SUPERSEDE, Step.HOST_RUNS_AGAIN])
    def test_every_sequence_keeps_the_session_invariants(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
