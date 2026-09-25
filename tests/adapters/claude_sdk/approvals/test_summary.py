from __future__ import annotations

from band.adapters.claude_sdk import ClaudeSDKAdapter


class TestApprovalSummary:
    """Tests for _approval_summary()."""

    def test_command_tool_shows_command(self):
        summary = ClaudeSDKAdapter._approval_summary("Bash", {"command": "rm -rf /tmp"})
        assert "rm -rf /tmp" in summary

    def test_file_tool_shows_path(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Edit", {"file_path": "/src/main.py"}
        )
        assert "/src/main.py" in summary

    def test_fallback_to_tool_name(self):
        summary = ClaudeSDKAdapter._approval_summary("SomeTool", {})
        assert summary == "SomeTool"

    def test_redacts_api_key_in_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "curl -H token=sk-abc123 https://api.example.com"}
        )
        assert "sk-abc123" not in summary
        assert "***" in summary

    def test_redacts_password_in_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "mysql -u root password=s3cret db"}
        )
        assert "s3cret" not in summary
        assert "***" in summary

    def test_preserves_safe_command(self):
        summary = ClaudeSDKAdapter._approval_summary(
            "Bash", {"command": "ls -la /home/user"}
        )
        assert "ls -la /home/user" in summary
