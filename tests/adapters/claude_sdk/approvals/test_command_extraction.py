from __future__ import annotations

from band.adapters.claude_sdk import ClaudeSDKAdapter


class TestCommandExtraction:
    """Tests for _extract_command()."""

    def test_extracts_approve_command(self):
        assert ClaudeSDKAdapter._extract_command("/approve a-1") == ("approve", "a-1")

    def test_extracts_decline_command(self):
        assert ClaudeSDKAdapter._extract_command("/decline a-2") == ("decline", "a-2")

    def test_extracts_approvals_list(self):
        assert ClaudeSDKAdapter._extract_command("/approvals") == ("approvals", "")

    def test_extracts_status_command(self):
        assert ClaudeSDKAdapter._extract_command("/status") == ("status", "")

    def test_returns_none_for_normal_message(self):
        assert ClaudeSDKAdapter._extract_command("Hello, agent!") is None

    def test_case_insensitive(self):
        assert ClaudeSDKAdapter._extract_command("/Approve a-1") == ("approve", "a-1")

    def test_bare_word_not_matched(self):
        """Bare words like 'approve' without / prefix should not match."""
        assert ClaudeSDKAdapter._extract_command("approve a-1") is None

    def test_command_not_matched_mid_sentence(self):
        """Commands embedded in natural text should not be intercepted."""
        assert ClaudeSDKAdapter._extract_command("hey /approve a-1") is None

    def test_command_with_leading_whitespace(self):
        """Leading whitespace should be ignored."""
        assert ClaudeSDKAdapter._extract_command("  /approve a-1") == ("approve", "a-1")

    def test_command_after_leading_mention_block(self):
        """A delivered reply arrives with the platform's ``@handle`` mention
        prepended (a reply must mention the agent), so the command follows it.
        The block is stripped so the command still matches -- without this the
        chat-approval reply was silently forwarded to the model as a prompt."""
        assert ClaudeSDKAdapter._extract_command("@alex/claude /approve a-1") == (
            "approve",
            "a-1",
        )
        # A human typing an inline mention doubles the token; still recognized.
        assert ClaudeSDKAdapter._extract_command(
            "@alex/claude @alex/claude /decline a-2"
        ) == (
            "decline",
            "a-2",
        )

    def test_approve_without_token(self):
        assert ClaudeSDKAdapter._extract_command("/approve") == ("approve", "")

    def test_multiple_slashes_not_matched(self):
        """///approve should not be treated as /approve."""
        assert ClaudeSDKAdapter._extract_command("///approve a-1") is None
