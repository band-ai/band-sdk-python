"""Agent runner forwarding."""

from __future__ import annotations

import asyncio

import pytest

from band.platform.event import (
    ContactAddedEvent,
    ParticipantRemovedEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
)
from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    GatedForwarder,
    Mark,
    build_runner,
    make_message_event,
    make_participant_added_event,
)


class TestAgentRunnerForwarding:
    async def test_forwards_message_event(self) -> None:
        runner, fwd, _ = build_runner()
        await runner._safe_handle_event(make_message_event())

        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1
        payload = fwd.forwarded[0]
        assert payload["event_type"] == "message_created"
        assert payload["agent_id"] == "agent-1"
        assert payload["room_id"] == "r1"
        assert payload["payload"]["id"] == "m1"
        assert payload["raw"]["id"] == "m1"
        assert "forwarded_at" in payload

    async def test_forwards_participant_added(self) -> None:
        runner, fwd, _ = build_runner()
        await runner._safe_handle_event(make_participant_added_event())

        assert len(fwd.forwarded) == 1
        assert fwd.forwarded[0]["event_type"] == "participant_added"

    async def test_dedups_message_by_id(self) -> None:
        runner, fwd, _ = build_runner()
        event = make_message_event(message_id="m-dup")

        await runner._safe_handle_event(event)
        await runner._safe_handle_event(event)
        await runner._safe_handle_event(event)

        assert len(fwd.forwarded) == 1

    async def test_dedup_does_not_cross_different_messages(self) -> None:
        runner, fwd, _ = build_runner()

        await runner._safe_handle_event(make_message_event(message_id="m1"))
        await runner._safe_handle_event(make_message_event(message_id="m2"))

        assert len(fwd.forwarded) == 2

    async def test_does_not_filter_self_messages(self) -> None:
        """Dumb pipe: bridge forwards all messages, even from the agent itself.

        Self-message filtering is Band logic — it lives in the SDK inside the
        container, not in the bridge.
        """
        runner, fwd, _ = build_runner(agent_id="agent-1")
        await runner._safe_handle_event(make_message_event(sender_id="agent-1"))

        assert len(fwd.forwarded) == 1

    @pytest.mark.parametrize(
        "messages_only, expected_types",
        [
            (True, ["message_created"]),
            (False, ["participant_added", "message_created"]),
        ],
    )
    async def test_message_filter_preserves_message_delivery(
        self, messages_only: bool, expected_types: list[str]
    ) -> None:
        runner, fwd, _ = build_runner(messages_only=messages_only)
        await runner._safe_handle_event(make_participant_added_event())
        await runner._safe_handle_event(make_message_event())
        assert [p["event_type"] for p in fwd.forwarded] == expected_types

    @pytest.mark.parametrize(
        "event",
        [
            ContactAddedEvent(),
            ParticipantRemovedEvent(room_id="r1"),
            RoomAddedEvent(room_id="r1"),
            RoomRemovedEvent(room_id="r1"),
            RoomDeletedEvent(room_id="r1"),
        ],
    )
    async def test_message_only_skips_nonmessage_classes(self, event: object) -> None:
        runner, forwarder, _ = build_runner(messages_only=True)
        await runner._safe_handle_event(event)
        assert forwarder.forwarded == []

    async def test_message_only_skip_does_not_wait_behind_room_work(self) -> None:
        forwarder = GatedForwarder(("held",))
        runner, _, _ = build_runner(
            forwarder=forwarder, messages_only=True, max_concurrent_forwards=1
        )
        held = asyncio.create_task(
            runner._safe_handle_event(make_message_event("held", "r1"))
        )
        try:
            await forwarder.started["held"].wait()
            await runner._safe_handle_event(make_participant_added_event("r1"))
            await runner._safe_handle_event(make_participant_added_event("r2"))
            assert forwarder.events == [(Mark.START, "held")]
        finally:
            forwarder.release["held"].set()
            await held
