"""ExecutionContextParticipants behavior."""

from __future__ import annotations

import pytest

from band.logging_config import TRACE_CONTEXT, trace_context_scope
from band.runtime.execution import (
    ExecutionContext,
)


class TestExecutionContextParticipants:
    """Test participant management."""

    def test_add_participant(self, mock_link, mock_handler):
        """add_participant() should add to list."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = ctx.add_participant(
            {
                "id": "user-1",
                "name": "Test User",
                "type": "User",
            }
        )

        assert result is True
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "Test User"

    def test_add_participant_deduplicates(self, mock_link, mock_handler):
        """add_participant() should not add a duplicate id, but should refresh
        its fields in place (e.g. a description learned after first tracking)."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        participant_id = "user-1"
        updated_name = "User One Updated"

        ctx.add_participant({"id": participant_id, "name": "User One", "type": "User"})
        result = ctx.add_participant(
            {"id": participant_id, "name": updated_name, "type": "User"}
        )

        assert result is False
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == updated_name

    def test_add_participant_merges_sparse_refresh(self, mock_link, mock_handler):
        """A sparser source (e.g. a WS payload without description/handle) must
        not erase fields an earlier, richer source already learned."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant(
            {
                "id": "agent-1",
                "name": "Role Bot",
                "type": "Agent",
                "handle": "org/role",
                "description": "Handles billing",
            }
        )

        ctx.add_participant({"id": "agent-1", "name": "Role Bot", "type": "Agent"})

        assert ctx.participants[0]["handle"] == "org/role"
        assert ctx.participants[0]["description"] == "Handles billing"

    def test_set_participants_replaces_membership_and_merges_fields(
        self, mock_link, mock_handler
    ):
        """set_participants() follows the snapshot's membership exactly, but a
        field the snapshot omits (the list endpoint has no description) keeps
        its previously learned value."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant(
            {
                "id": "agent-1",
                "name": "Role Bot",
                "type": "Agent",
                "description": "Handles billing",
            }
        )
        ctx.add_participant({"id": "user-9", "name": "Departed", "type": "User"})

        ctx.set_participants(
            [
                {"id": "agent-1", "name": "Role Bot", "type": "Agent"},
                {"id": "user-2", "name": "New User", "type": "User"},
            ]
        )

        assert [p["id"] for p in ctx.participants] == ["agent-1", "user-2"]
        assert ctx.participants[0]["description"] == "Handles billing"

    def test_remove_participant(self, mock_link, mock_handler):
        """remove_participant() should remove from list."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User", "type": "User"})

        result = ctx.remove_participant("user-1")

        assert result is True
        assert len(ctx.participants) == 0

    def test_remove_participant_not_found(self, mock_link, mock_handler):
        """remove_participant() should return False if not found."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = ctx.remove_participant("nonexistent")

        assert result is False

    def test_participants_changed_true_initially(self, mock_link, mock_handler):
        """participants_changed() should return True initially."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.participants_changed() is True

    def test_participants_changed_false_after_mark(self, mock_link, mock_handler):
        """participants_changed() should return False after mark_participants_sent()."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User", "type": "User"})
        ctx.mark_participants_sent()

        assert ctx.participants_changed() is False

    def test_participants_changed_true_after_add(self, mock_link, mock_handler):
        """participants_changed() should return True after adding participant."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User 1", "type": "User"})
        ctx.mark_participants_sent()
        ctx.add_participant({"id": "user-2", "name": "User 2", "type": "User"})

        assert ctx.participants_changed() is True

    def test_participants_changed_true_after_remove(self, mock_link, mock_handler):
        """participants_changed() should return True after a removal."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User 1", "type": "User"})
        ctx.mark_participants_sent()
        ctx.remove_participant("user-1")

        assert ctx.participants_changed() is True

    def test_participants_changed_true_after_field_refresh(
        self, mock_link, mock_handler
    ):
        """A same-membership refresh (e.g. a description learned later) must
        still count as changed, or the LLM never sees the new field."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User 1", "type": "User"})
        ctx.mark_participants_sent()

        ctx.add_participant(
            {
                "id": "user-1",
                "name": "User 1",
                "type": "User",
                "description": "Handles billing",
            }
        )

        assert ctx.participants_changed() is True

    def test_participants_changed_true_after_pure_reorder(
        self, mock_link, mock_handler
    ):
        """band_sdk_core.ParticipantRoster.changed() is order-sensitive: the
        exact same membership, resent in a different order, must still report
        changed -- a deliberate behavior change from the old Python
        id-keyed-dict comparison, which was order-insensitive."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.set_participants(
            [
                {"id": "user-1", "name": "User 1", "type": "User"},
                {"id": "user-2", "name": "User 2", "type": "User"},
            ]
        )
        ctx.mark_participants_sent()
        assert ctx.participants_changed() is False

        ctx.set_participants(
            [
                {"id": "user-2", "name": "User 2", "type": "User"},
                {"id": "user-1", "name": "User 1", "type": "User"},
            ]
        )

        assert ctx.participants_changed() is True

    def test_set_participants_duplicate_id_raises_and_leaves_roster_untouched(
        self, mock_link, mock_handler
    ):
        """A duplicate id in the authoritative snapshot must reject the whole
        snapshot with a ValueError naming the repeated id in .issues, leaving
        the previous roster in place rather than partially applying it."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.set_participants([{"id": "user-1", "name": "User One", "type": "User"}])

        with pytest.raises(ValueError) as exc_info:
            ctx.set_participants(
                [
                    {"id": "user-2", "name": "User Two", "type": "User"},
                    {"id": "user-2", "name": "User Two Dup", "type": "User"},
                ]
            )

        issues = exc_info.value.issues
        assert any("user-2" in issue[2] for issue in issues)
        assert [p["id"] for p in ctx.participants] == ["user-1"]

    def test_set_participants_duplicate_id_error_carries_the_turn_trace_context(
        self, mock_link, mock_handler
    ):
        """set_participants passes the ambient per-turn TRACE_CONTEXT into
        set_all, not a hardcoded None -- the duplicate-id error must carry
        whichever turn actually called it."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        duplicates = [
            {"id": "user-1", "name": "User One", "type": "User"},
            {"id": "user-1", "name": "User One Dup", "type": "User"},
        ]

        with trace_context_scope():
            active = TRACE_CONTEXT.get()
            with pytest.raises(ValueError) as exc_info:
                ctx.set_participants(duplicates)
        assert exc_info.value.trace_context == active

        with pytest.raises(ValueError) as exc_info:
            ctx.set_participants(duplicates)
        assert exc_info.value.trace_context is None
