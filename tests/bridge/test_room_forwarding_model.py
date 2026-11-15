"""Generated room-forwarding sequences preserve ownership, cancellation and capacity."""

from __future__ import annotations

import asyncio

from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests.bridge.lifecycle import Step, play, sequences


class TestRoomForwardingModel:
    @settings(max_examples=60, deadline=None)
    @given(steps=sequences, capacity=st.integers(min_value=1, max_value=2))
    @example(
        steps=[
            Step.ACCEPT_A,
            Step.ACCEPT_A,
            Step.ACCEPT_B,
            Step.CANCEL_A,
            Step.ACCEPT_A,
            Step.DRAIN,
        ],
        capacity=1,
    )
    @example(
        steps=[Step.ACCEPT_A, Step.DRAIN, Step.ACCEPT_A, Step.CANCEL_A], capacity=2
    )
    @example(
        steps=[Step.ACCEPT_A, Step.ACCEPT_B, Step.CANCEL_B, Step.ACCEPT_B, Step.DRAIN],
        capacity=1,
    )
    @example(
        steps=[
            Step.ACCEPT_GLOBAL,
            Step.ACCEPT_GLOBAL,
            Step.ACCEPT_A,
            Step.CANCEL_A,
            Step.DRAIN,
        ],
        capacity=1,
    )
    @example(
        steps=[
            Step.ACCEPT_A,
            Step.ACCEPT_B,
            Step.RELEASE_A,
            Step.ACCEPT_A,
            Step.RELEASE_B,
            Step.DRAIN,
        ],
        capacity=1,
    )
    def test_sequences_preserve_room_ownership(
        self, steps: list[Step], capacity: int
    ) -> None:
        asyncio.run(play(steps, capacity))
