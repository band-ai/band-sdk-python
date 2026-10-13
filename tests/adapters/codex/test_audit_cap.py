"""Codex adapter: audit cap."""

from __future__ import annotations

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


class TestAuditCap:
    def test_audit_trail_capped_at_limit(self) -> None:
        """Approval audit trail is capped at max_approval_audit_per_room."""
        adapter = make_codex_adapter(
            FakeCodexClient(),
            config=CodexAdapterConfig(max_approval_audit_per_room=5),
        )
        for i in range(10):
            adapter._record_approval_audit(
                room_id="room-1",
                request_id=str(i),
                method="item/commandExecution/requestApproval",
                decision="accept",
                decided_by="test",
            )
        audit = adapter._approval_audit["room-1"]
        assert len(audit) == 5
        # Should keep the most recent entries
        assert audit[0].request_id == "5"
        assert audit[-1].request_id == "9"

    def test_session_approved_capped_at_limit(self) -> None:
        """Session approvals evict LRU when max_session_approved_per_room is hit."""
        adapter = make_codex_adapter(
            FakeCodexClient(),
            config=CodexAdapterConfig(max_session_approved_per_room=3),
        )
        for i in range(5):
            adapter._record_session_approval("room-1", f"commandExecution:cmd{i}")
        room = adapter._session_approved["room-1"]
        assert list(room.keys()) == [
            "commandExecution:cmd2",
            "commandExecution:cmd3",
            "commandExecution:cmd4",
        ]

    def test_session_approval_reinsert_moves_to_end(self) -> None:
        """Re-approving an existing key moves it to the most-recent slot."""
        adapter = make_codex_adapter(
            FakeCodexClient(),
            config=CodexAdapterConfig(max_session_approved_per_room=3),
        )
        adapter._record_session_approval("room-1", "commandExecution:a")
        adapter._record_session_approval("room-1", "commandExecution:b")
        adapter._record_session_approval("room-1", "commandExecution:c")
        # Re-approve the oldest — it should move to the end.
        adapter._record_session_approval("room-1", "commandExecution:a")
        adapter._record_session_approval("room-1", "commandExecution:d")
        room = adapter._session_approved["room-1"]
        # "b" is the oldest after re-approving "a", so it's the one evicted.
        assert "commandExecution:b" not in room
        assert list(room.keys()) == [
            "commandExecution:c",
            "commandExecution:a",
            "commandExecution:d",
        ]
