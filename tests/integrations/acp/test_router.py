"""Tests for AgentRouter."""

from __future__ import annotations

from band.integrations.acp.router import AgentRouter


class TestAgentRouter:
    """Tests for AgentRouter.resolve()."""

    def test_slash_command_routing(self) -> None:
        """Should route via slash commands."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer", "claude": "claude-peer"},
        )

        text, peer = router.resolve("/codex fix the bug")

        assert text == "fix the bug"
        assert peer == "codex-peer"

    def test_slash_command_case_insensitive(self) -> None:
        """Should match slash commands case-insensitively."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer"},
        )

        text, peer = router.resolve("/CODEX fix it")

        assert text == "fix it"
        assert peer == "codex-peer"

    def test_slash_command_normalizes_config_keys(self) -> None:
        """Should match slash commands even when config keys are mixed-case."""
        router = AgentRouter(
            slash_commands={"Codex": "codex-peer"},
        )

        text, peer = router.resolve("/codex fix it")

        assert text == "fix it"
        assert peer == "codex-peer"

    def test_slash_command_no_body(self) -> None:
        """Should handle slash command with no body text."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer"},
        )

        text, peer = router.resolve("/codex")

        assert text == ""
        assert peer == "codex-peer"

    def test_slash_command_unknown(self) -> None:
        """Should not route unknown slash commands."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer"},
        )

        text, peer = router.resolve("/unknown fix bug")

        assert text == "/unknown fix bug"
        assert peer is None

    def test_mode_based_routing(self) -> None:
        """Should route based on current mode."""
        router = AgentRouter(
            mode_to_peer={"code": "codex-peer", "chat": "chat-peer"},
        )

        text, peer = router.resolve("fix the bug", current_mode="code")

        assert text == "fix the bug"
        assert peer == "codex-peer"

    def test_mode_unknown(self) -> None:
        """Should not route when mode is not in mapping."""
        router = AgentRouter(
            mode_to_peer={"code": "codex-peer"},
        )

        text, peer = router.resolve("hello", current_mode="debug")

        assert text == "hello"
        assert peer is None

    def test_default_routing(self) -> None:
        """Should return None peer when no route matches."""
        router = AgentRouter()

        text, peer = router.resolve("hello world")

        assert text == "hello world"
        assert peer is None

    def test_empty_config(self) -> None:
        """Should work with no configuration."""
        router = AgentRouter()

        text, peer = router.resolve("/codex fix")

        assert text == "/codex fix"
        assert peer is None

    def test_slash_command_priority_over_mode(self) -> None:
        """Slash commands should take priority over mode routing."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer"},
            mode_to_peer={"code": "other-peer"},
        )

        text, peer = router.resolve("/codex fix", current_mode="code")

        assert text == "fix"
        assert peer == "codex-peer"

    def test_non_slash_text_with_mode(self) -> None:
        """Regular text should use mode routing when available."""
        router = AgentRouter(
            slash_commands={"codex": "codex-peer"},
            mode_to_peer={"code": "mode-peer"},
        )

        text, peer = router.resolve("fix the bug", current_mode="code")

        assert text == "fix the bug"
        assert peer == "mode-peer"
