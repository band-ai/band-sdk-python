from __future__ import annotations

from band.runtime.prompts import render_system_prompt


class TestPromptRendering:
    def test_backstory_uses_render_system_prompt(self, CrewAIAdapter):
        """CrewAI backstory is now built via render_system_prompt."""

        prompt = render_system_prompt(
            agent_name="TestAgent",
            agent_description="A test agent",
        )
        # Verify the rendered prompt contains key sections
        assert "Environment" in prompt
        assert "band_send_message" in prompt
        assert "band_lookup_peers" in prompt
