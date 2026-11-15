"""ADKAgentNameSanitization behavior of the Google ADK adapter."""

from __future__ import annotations

from tests.adapters.googleadk.bridge import _sanitize_adk_agent_name


class TestADKAgentNameSanitization:
    """Tests for ADK-safe internal agent names."""

    def test_replaces_spaces_and_punctuation(self):
        """Display names with punctuation should become valid ADK identifiers."""
        assert _sanitize_adk_agent_name("Band Agent!") == "Band_Agent_"

    def test_prefixes_digit_leading_names(self):
        """ADK rejects identifiers that start with digits, even after punctuation replacement."""
        safe_name = _sanitize_adk_agent_name("24/7 Support")

        assert safe_name == "band_24_7_Support"
        assert safe_name.isidentifier()

    def test_avoids_reserved_user_name(self):
        """ADK reserves the exact agent name 'user' for end-user input."""
        assert _sanitize_adk_agent_name("user") == "band_user"

    def test_uses_default_for_empty_name(self):
        """Empty names should still produce the default ADK-safe identifier."""
        assert _sanitize_adk_agent_name("") == "band_agent"
