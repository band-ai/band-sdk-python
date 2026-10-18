"""Two events in the same room must serialize through the forwarder; two events in different rooms must overlap."""

from __future__ import annotations

import asyncio

from tests.bridge.kit import (
    OrderTrackingForwarder,
    build_runner,
    make_message_event,
)


class TestAgentRunnerPerRoomSerialization:
    """Two events in the same room must serialize through the forwarder;
    two events in different rooms must overlap.

    Regression test for the duplicate-reply race seen during INT-506 deploy:
    two PA messages arriving back-to-back in the same room invoked the
    container twice in parallel; both invocations fetched history before
    either reply landed and both LLMs emitted duplicate responses.
    """

    async def test_same_room_serializes(self) -> None:
        fwd = OrderTrackingForwarder(delay=0.05)
        runner, _, _ = build_runner(forwarder=fwd)

        e1 = make_message_event(message_id="m1", room_id="room-X")
        e2 = make_message_event(message_id="m2", room_id="room-X")

        await asyncio.gather(
            runner._safe_handle_event(e1),
            runner._safe_handle_event(e2),
        )

        # With per-room serialization, one fully completes before the other
        # starts. Reject any interleaving.
        assert fwd.events in (
            [("start", "m1"), ("end", "m1"), ("start", "m2"), ("end", "m2")],
            [("start", "m2"), ("end", "m2"), ("start", "m1"), ("end", "m1")],
        ), f"Expected serial ordering, got: {fwd.events}"

    async def test_different_rooms_overlap(self) -> None:
        fwd = OrderTrackingForwarder(delay=0.05)
        runner, _, _ = build_runner(forwarder=fwd)

        e1 = make_message_event(message_id="m1", room_id="room-A")
        e2 = make_message_event(message_id="m2", room_id="room-B")

        await asyncio.gather(
            runner._safe_handle_event(e1),
            runner._safe_handle_event(e2),
        )

        # With different rooms, both forwards overlap: both start before
        # either ends.
        kinds = [e[0] for e in fwd.events]
        assert kinds == ["start", "start", "end", "end"], (
            f"Expected overlapping ordering, got: {fwd.events}"
        )
