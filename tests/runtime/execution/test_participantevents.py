from __future__ import annotations

from band.runtime.execution import (
    ExecutionContext,
)
from tests.conftest import (
    make_participant_added_event,
    make_participant_removed_event,
)
from tests.runtime.conftest import wait_for_condition
from tests.runtime.scheduler.driver import attempt_delivery


class TestExecutionContextParticipantEvents:
    """Test participant event handling."""

    async def test_participant_added_event_updates_list(self, mock_link, mock_handler):
        """participant_added event should update participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await ctx.on_event(event)
        await wait_for_condition(lambda: ctx.queue.qsize() == 0)

        assert any(p["id"] == "user-2" for p in ctx.participants)

        await ctx.stop()

    async def test_participant_removed_event_updates_list(
        self, mock_link, mock_handler
    ):
        """participant_removed event should update participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User One", "type": "User"})

        await ctx.start()

        event = make_participant_removed_event(
            room_id="room-123",
            participant_id="user-1",
        )
        await ctx.on_event(event)
        await wait_for_condition(lambda: ctx.queue.qsize() == 0)

        assert not any(p["id"] == "user-1" for p in ctx.participants)

        await ctx.stop()

    async def test_participant_added_visible_in_same_cycle_context(
        self, mock_link, mock_handler
    ):
        """A participant_added event applied inside _process_event_body must
        be visible in the same-cycle get_context() call that follows it --
        without a second REST fetch. build_context() refreshes participants
        from the live roster on every call instead of returning whatever
        snapshot was baked in at hydrate time."""
        captured: list[str] = []

        async def handler(ctx, event):
            context = await ctx.get_context()
            captured.extend(p["id"] for p in context.participants)

        ctx = ExecutionContext("room-123", mock_link, handler)
        await ctx.hydrate()  # seeds the roster with user-1 (mock_link fixture)

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await attempt_delivery(ctx, event)

        assert "user-1" in captured
        assert "user-2" in captured
        assert mock_link.rest.agent_api_context.get_agent_chat_context.call_count == 1
