"""Agent runner forwarding."""

from __future__ import annotations

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    make_message_event,
    make_participant_added_event,
)


class TestAgentRunnerForwarding:
    async def test_forwards_message_event(self) -> None:
        runner, fwd, _ = build_runner()
        await runner._handle_event(make_message_event())

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
        await runner._handle_event(make_participant_added_event())

        assert len(fwd.forwarded) == 1
        assert fwd.forwarded[0]["event_type"] == "participant_added"

    async def test_dedups_message_by_id(self) -> None:
        runner, fwd, _ = build_runner()
        event = make_message_event(message_id="m-dup")

        await runner._handle_event(event)
        await runner._handle_event(event)
        await runner._handle_event(event)

        assert len(fwd.forwarded) == 1

    async def test_dedup_does_not_cross_different_messages(self) -> None:
        runner, fwd, _ = build_runner()

        await runner._handle_event(make_message_event(message_id="m1"))
        await runner._handle_event(make_message_event(message_id="m2"))

        assert len(fwd.forwarded) == 2

    async def test_does_not_filter_self_messages(self) -> None:
        """Dumb pipe: bridge forwards all messages, even from the agent itself.

        Self-message filtering is Band logic — it lives in the SDK inside the
        container, not in the bridge.
        """
        runner, fwd, _ = build_runner(agent_id="agent-1")
        await runner._handle_event(make_message_event(sender_id="agent-1"))

        assert len(fwd.forwarded) == 1
