"""Codex adapter: _record_approval_audit API surface."""

from __future__ import annotations

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)


class TestApprovalAuditRecording:
    """_record_approval_audit API surface."""

    def test_record_approval_audit_returns_entry(self) -> None:
        """_record_approval_audit returns the entry it appended."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        entry = adapter._record_approval_audit(
            room_id="room-1",
            request_id="req-1",
            method="item/commandExecution/requestApproval",
            decision="accept",
            decided_by="tester",
            summary="command: ls",
        )
        assert entry.request_id == "req-1"
        assert entry.decision == "accept"
        assert adapter._approval_audit["room-1"][-1] is entry
