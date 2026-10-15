from __future__ import annotations

from band.runtime.tools import TurnEffect, turn_effect


class TestReplyEffectSpellings:
    """Which tool names resolve to Band's reply effect."""

    def test_sdk_injected_tool(self) -> None:
        assert turn_effect("band_send_message") is TurnEffect.REPLY

    def test_standalone_band_mcp_tool(self) -> None:
        assert turn_effect("create_agent_chat_message") is TurnEffect.REPLY

    def test_mcp_server_prefixed_names(self) -> None:
        """MCP clients may prefix the server name onto the tool name."""
        assert turn_effect("band-band_send_message") is TurnEffect.REPLY
        assert turn_effect("band-create_agent_chat_message") is TurnEffect.REPLY

    def test_non_settling_tools(self) -> None:
        assert turn_effect("band_send_event") is TurnEffect.OBSERVE
        assert turn_effect("band_lookup_peers") is TurnEffect.OBSERVE
        assert turn_effect("get_weather") is TurnEffect.OBSERVE

    def test_no_substring_false_positive(self) -> None:
        """Only an exact or server-prefixed match counts, not any substring."""
        assert turn_effect("band_send_message_draft") is TurnEffect.OBSERVE

    def test_non_band_server_prefix_does_not_resolve(self) -> None:
        """An unrelated MCP server's own tool must never be treated as a Band
        reply just because it ends in ``-band_send_message``."""
        assert turn_effect("other-band_send_message") is TurnEffect.OBSERVE
