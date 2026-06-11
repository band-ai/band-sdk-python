"""Tests for role-based prompt profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.prompts import get_available_roles, get_role_prompt, load_role_prompt
from band.prompts.roles import CONVERSATION_DISCIPLINE


class TestConversationDiscipline:
    """Tests for the shared conversation discipline block."""

    def test_has_rule_priority(self) -> None:
        """Test that rule priority is explicitly defined."""
        assert "Rule priority (highest to lowest):" in CONVERSATION_DISCIPLINE
        assert "1." in CONVERSATION_DISCIPLINE
        assert "2." in CONVERSATION_DISCIPLINE
        assert "3." in CONVERSATION_DISCIPLINE

    def test_mention_overrides_silence(self) -> None:
        """Test that @mention override is the highest priority rule."""
        assert "overrides every" in CONVERSATION_DISCIPLINE
        assert "you MUST respond" in CONVERSATION_DISCIPLINE

    def test_has_mention_detection(self) -> None:
        """Test that mention detection rules are defined."""
        assert "Mention detection:" in CONVERSATION_DISCIPLINE
        assert "@dataclass" in CONVERSATION_DISCIPLINE
        assert "email addresses" in CONVERSATION_DISCIPLINE

    def test_has_anti_ping_pong(self) -> None:
        """Test that anti-ping-pong rule exists."""
        assert "do NOT @mention the sender" in CONVERSATION_DISCIPLINE
        assert "mention loops" in CONVERSATION_DISCIPLINE

    def test_has_missing_inputs_guidance(self) -> None:
        """Test guidance for when @mentioned but missing context."""
        assert "missing inputs" in CONVERSATION_DISCIPLINE
        assert "acknowledgment" in CONVERSATION_DISCIPLINE

    def test_has_event_vs_message_guidance(self) -> None:
        """Test guidance on using events vs messages."""
        assert "band_send_event" in CONVERSATION_DISCIPLINE

    def test_injected_into_all_roles(self) -> None:
        """Test that the shared block appears in every role prompt."""
        for role in get_available_roles():
            prompt = get_role_prompt(role)
            assert "Rule priority (highest to lowest):" in prompt
            assert "Mention detection:" in prompt


class TestGetRolePrompt:
    """Tests for get_role_prompt function."""

    def test_get_planner_role(self) -> None:
        """Test getting planner role prompt."""
        prompt = get_role_prompt("planner")

        assert "Role: Planner" in prompt
        assert "Design Document" in prompt
        assert "Planning complete" in prompt

    def test_get_reviewer_role(self) -> None:
        """Test getting reviewer role prompt."""
        prompt = get_role_prompt("reviewer")

        assert "Role: Code Reviewer" in prompt
        assert "Review Checklist" in prompt
        assert "[Critical]" in prompt
        assert "[Suggestion]" in prompt
        assert "Approved. Ready to merge." in prompt

    def test_custom_agent_name(self) -> None:
        """Test that custom agent name is used in prompt."""
        prompt = get_role_prompt("planner", agent_name="Design Bot")

        assert "Design Bot" in prompt

    def test_default_agent_name(self) -> None:
        """Test that default agent name is used when not specified."""
        prompt = get_role_prompt("planner")

        assert "You are Planner" in prompt

    def test_unknown_role_raises_error(self) -> None:
        """Test that unknown role raises ValueError with helpful message."""
        with pytest.raises(ValueError) as exc_info:
            get_role_prompt("unknown_role")

        assert "Unknown role 'unknown_role'" in str(exc_info.value)
        assert "Available roles:" in str(exc_info.value)
        assert "planner" in str(exc_info.value)

    def test_all_available_roles_work(self) -> None:
        """Test that all roles in get_available_roles() can be loaded."""
        for role in get_available_roles():
            prompt = get_role_prompt(role)
            assert isinstance(prompt, str)
            assert len(prompt) > 100


class TestGetAvailableRoles:
    """Tests for get_available_roles function."""

    def test_contains_expected_roles(self) -> None:
        """Test that expected roles are available."""
        roles = get_available_roles()
        assert "planner" in roles
        assert "reviewer" in roles

    def test_returns_list(self) -> None:
        """Test that get_available_roles returns a list."""
        assert isinstance(get_available_roles(), list)

    def test_all_strings(self) -> None:
        """Test that all roles are strings."""
        for role in get_available_roles():
            assert isinstance(role, str)


class TestLoadRolePrompt:
    """Tests for load_role_prompt function."""

    def test_loads_builtin_role(self) -> None:
        """Test loading a built-in role without prompt_dir."""
        prompt = load_role_prompt("planner")
        assert prompt is not None
        assert "Role: Planner" in prompt

    def test_returns_none_for_unknown_role(self) -> None:
        """Test that unknown role returns None."""
        result = load_role_prompt("nonexistent_role")
        assert result is None

    def test_loads_from_prompt_dir(self, tmp_path: Path) -> None:
        """Test loading role from file in prompt_dir."""
        prompt_dir = Path(str(tmp_path))
        prompt_file = prompt_dir / "custom.md"
        prompt_file.write_text("Custom Role Prompt")

        result = load_role_prompt("custom", prompt_dir)
        assert result == "Custom Role Prompt"

    def test_file_overrides_builtin(self, tmp_path: Path) -> None:
        """Test that file in prompt_dir overrides built-in role."""
        prompt_dir = Path(str(tmp_path))
        prompt_file = prompt_dir / "planner.md"
        prompt_file.write_text("Custom Planner Override")

        result = load_role_prompt("planner", prompt_dir)
        assert result == "Custom Planner Override"

    def test_falls_back_to_builtin_when_file_missing(self, tmp_path: Path) -> None:
        """Test fallback to built-in when file not in prompt_dir."""
        prompt_dir = Path(str(tmp_path))
        result = load_role_prompt("planner", prompt_dir)
        assert result is not None
        assert "Role: Planner" in result


class TestRoleSpecificRules:
    """Tests for role-specific rules that differ from the shared base."""

    def test_planner_has_design_doc_structure(self) -> None:
        """Test that planner includes design document structure."""
        prompt = get_role_prompt("planner")

        assert "Summary" in prompt
        assert "Problem Statement" in prompt
        assert "Technical Design" in prompt
        assert "Testing Strategy" in prompt

    def test_planner_has_human_escalation(self) -> None:
        """Test that planner includes when to involve humans."""
        prompt = get_role_prompt("planner")

        assert "When to Involve Humans" in prompt
        assert "Architecture decisions" in prompt
        assert "Security-sensitive" in prompt

    def test_planner_is_default_speaker(self) -> None:
        """Test that planner responds to unaddressed human messages."""
        prompt = get_role_prompt("planner")
        assert "without @mentioning a specific" in prompt
        assert "you should respond and coordinate" in prompt

    def test_reviewer_defers_unaddressed_messages(self) -> None:
        """Test that reviewer waits for delegation on unaddressed messages."""
        prompt = get_role_prompt("reviewer")
        assert "do NOT respond unless it is clearly a review request" in prompt

    def test_planner_allows_agent_workstream_continuation(self) -> None:
        """Test planner can respond to agents within active workstreams."""
        prompt = get_role_prompt("planner")
        assert "Only start NEW workstreams when a HUMAN asks" in prompt
        assert "respond to questions or tasks from other agents" in prompt

    def test_reviewer_has_feedback_format(self) -> None:
        """Test that reviewer includes feedback format guidance."""
        prompt = get_role_prompt("reviewer")

        assert "[Critical]" in prompt
        assert "[Suggestion]" in prompt
        assert "[Nit]" in prompt

    def test_all_roles_have_handoff_protocol(self) -> None:
        """Test that all roles have handoff guidance."""
        for role in get_available_roles():
            prompt = get_role_prompt(role)
            assert "wait silently" in prompt.lower()
