from __future__ import annotations

from band.adapters.claude_sdk import ClaudeSDKAdapter


class TestApprovalTokenCounter:
    """Tests for per-room approval token counters."""

    def test_tokens_are_per_room(self):
        """Each room should have its own incrementing counter."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter._next_approval_token("room-1") == "a-1"
        assert adapter._next_approval_token("room-1") == "a-2"
        assert adapter._next_approval_token("room-2") == "a-1"  # separate counter
        assert adapter._next_approval_token("room-1") == "a-3"

    def test_counter_persists_after_room_cleanup(self):
        """Counter should NOT reset on cleanup to avoid token collisions."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._next_approval_token("room-1")
        adapter._next_approval_token("room-1")
        adapter._clear_pending_approvals_for_room("room-1")
        # Counter continues from where it left off
        assert adapter._next_approval_token("room-1") == "a-3"
