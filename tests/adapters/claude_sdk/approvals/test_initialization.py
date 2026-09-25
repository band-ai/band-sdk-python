from __future__ import annotations

from band.adapters.claude_sdk import ClaudeSDKAdapter


class TestApprovalInitialization:
    """Tests for approval-related constructor defaults."""

    def test_approval_mode_defaults_to_none(self):
        adapter = ClaudeSDKAdapter()
        assert adapter.approval_mode is None

    def test_approval_mode_configurable(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter.approval_mode == "manual"

    def test_approval_config_defaults(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        assert adapter.approval_text_notifications is True
        assert adapter.approval_wait_timeout_s == 300.0
        assert adapter.approval_timeout_decision == "decline"
        assert adapter.max_pending_approvals_per_room == 50
