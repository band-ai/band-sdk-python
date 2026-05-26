"""
Tests for system prompt rendering.

Tests cover:
- render_system_prompt() with include_base_instructions parameter
- Capability-gated sections (memory, contacts)
- Injection defense section
- No false claims about internet access
"""

from thenvoi.core.types import AdapterFeatures, Capability
from thenvoi.runtime.prompts import BASE_INSTRUCTIONS, render_system_prompt


class TestRenderSystemPromptDefaults:
    """Test render_system_prompt() default behavior."""

    def test_includes_base_instructions_by_default(self):
        """Default should include BASE_INSTRUCTIONS."""
        prompt = render_system_prompt(
            agent_name="TestBot",
            agent_description="A test agent",
        )

        assert "## Environment" in prompt
        assert "Multi-participant chat" in prompt

    def test_includes_agent_identity(self):
        """Should include agent name and description."""
        prompt = render_system_prompt(
            agent_name="MyAgent",
            agent_description="A helpful assistant",
        )

        assert "MyAgent" in prompt
        assert "A helpful assistant" in prompt

    def test_includes_custom_section(self):
        """Should include custom section in Developer Instructions."""
        prompt = render_system_prompt(
            agent_name="TestBot",
            custom_section="Focus on Python programming.",
        )

        assert "Focus on Python programming." in prompt
        assert "## Developer Instructions" in prompt


class TestRenderSystemPromptWithoutBaseInstructions:
    """Test render_system_prompt() with include_base_instructions=False."""

    def test_excludes_base_instructions_when_disabled(self):
        """Should NOT include BASE_INSTRUCTIONS when disabled."""
        prompt = render_system_prompt(
            agent_name="TestBot",
            agent_description="A test agent",
            include_base_instructions=False,
        )

        assert "## Environment" not in prompt
        assert "## Delegation" not in prompt

    def test_still_includes_agent_identity_when_disabled(self):
        """Should still include agent name and description."""
        prompt = render_system_prompt(
            agent_name="MinimalBot",
            agent_description="A minimal agent",
            include_base_instructions=False,
        )

        assert "MinimalBot" in prompt
        assert "A minimal agent" in prompt

    def test_still_includes_custom_section_when_disabled(self):
        """Should still include custom section."""
        prompt = render_system_prompt(
            agent_name="TestBot",
            agent_description="Test",
            custom_section="Custom behavior instructions here.",
            include_base_instructions=False,
        )

        assert "Custom behavior instructions here." in prompt

    def test_minimal_prompt_format(self):
        """Minimal prompt should have expected format."""
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            custom_section="Be helpful.",
            include_base_instructions=False,
        )

        assert prompt.startswith("You are Bot, helper.")
        assert "Be helpful." in prompt

    def test_empty_custom_section_when_disabled(self):
        """Should handle empty custom section gracefully."""
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            custom_section="",
            include_base_instructions=False,
        )

        assert "Bot" in prompt
        assert "helper" in prompt


class TestBaseInstructionsConstant:
    """Test BASE_INSTRUCTIONS constant."""

    def test_base_instructions_exported(self):
        """BASE_INSTRUCTIONS should be importable."""
        assert BASE_INSTRUCTIONS is not None
        assert len(BASE_INSTRUCTIONS) > 0

    def test_base_instructions_has_key_sections(self):
        """BASE_INSTRUCTIONS should have expected sections."""
        assert "## Environment" in BASE_INSTRUCTIONS
        assert "## Security" in BASE_INSTRUCTIONS
        assert "## Delegation" in BASE_INSTRUCTIONS
        assert "## Relaying" in BASE_INSTRUCTIONS

    def test_no_false_internet_claims(self):
        """BASE_INSTRUCTIONS should not claim agent has no internet access."""
        assert "NO internet access" not in BASE_INSTRUCTIONS
        assert "no internet" not in BASE_INSTRUCTIONS.lower()

    def test_has_injection_defense(self):
        """BASE_INSTRUCTIONS should include injection defense guidance."""
        assert (
            "system instructions" in BASE_INSTRUCTIONS.lower()
            or "override" in BASE_INSTRUCTIONS.lower()
        )

    def test_no_forced_thought_events(self):
        """BASE_INSTRUCTIONS should not force thought event emission."""
        assert "MUST call" not in BASE_INSTRUCTIONS
        assert "ALWAYS Share Your Thinking" not in BASE_INSTRUCTIONS


class TestCapabilityGatedSections:
    """Test that capability sections are included/excluded based on features."""

    def test_memory_section_included_when_enabled(self):
        """Memory tool instructions included when Capability.MEMORY is set."""
        features = AdapterFeatures(capabilities={Capability.MEMORY})
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            features=features,
        )

        assert "## Memory Tools" in prompt
        assert "thenvoi_store_memory" in prompt

    def test_memory_section_absent_by_default(self):
        """Memory tool instructions absent when no features."""
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
        )

        assert "## Memory Tools" not in prompt

    def test_contacts_section_included_when_enabled(self):
        """Contact tool instructions included when Capability.CONTACTS is set."""
        features = AdapterFeatures(capabilities={Capability.CONTACTS})
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            features=features,
        )

        assert "## Contact Management Tools" in prompt

    def test_contacts_section_absent_by_default(self):
        """Contact tool instructions absent when no features."""
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
        )

        assert "## Contact Management Tools" not in prompt

    def test_both_capabilities_included(self):
        """Both sections included when both capabilities enabled."""
        features = AdapterFeatures(
            capabilities={Capability.MEMORY, Capability.CONTACTS}
        )
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            features=features,
        )

        assert "## Memory Tools" in prompt
        assert "## Contact Management Tools" in prompt

    def test_capability_sections_excluded_when_no_base_instructions(self):
        """Capability sections not included when include_base_instructions=False."""
        features = AdapterFeatures(capabilities={Capability.MEMORY})
        prompt = render_system_prompt(
            agent_name="Bot",
            agent_description="helper",
            features=features,
            include_base_instructions=False,
        )

        assert "## Memory Tools" not in prompt
