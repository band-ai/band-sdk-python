"""The hub room exposes contact tools whatever the adapter requests."""

from __future__ import annotations

from band.runtime.tools import (
    CONTACT_TOOL_NAMES,
    AgentTools,
)


class TestAgentToolsHubRoomAutoEnable:
    def test_is_hub_room_false_without_hub_id(self, mock_rest) -> None:
        tools = AgentTools("room-A", mock_rest)
        assert tools.is_hub_room is False

    def test_is_hub_room_false_when_room_does_not_match(self, mock_rest) -> None:
        tools = AgentTools("room-A", mock_rest, hub_room_id="hub-room-id")
        assert tools.is_hub_room is False

    def test_is_hub_room_true_when_room_matches(self, mock_rest) -> None:
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        assert tools.is_hub_room is True

    def test_non_hub_room_respects_include_contacts_false(self, mock_rest) -> None:
        """Adapters that gate contacts get them excluded in non-hub rooms."""
        tools = AgentTools("room-A", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_anthropic_tool_schemas(capabilities=frozenset())
        names = {s["name"] for s in schemas}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)

    def test_hub_room_force_includes_contacts_even_when_disabled(
        self, mock_rest
    ) -> None:
        """Hub room ignores an empty capability set and exposes contact tools anyway."""
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_anthropic_tool_schemas(capabilities=frozenset())
        names = {s["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names), (
            "Hub room must auto-enable contact tools even when the adapter "
            "requests an empty capability set"
        )

    def test_hub_room_includes_contacts_in_openai_format(self, mock_rest) -> None:
        tools = AgentTools("hub-room-id", mock_rest, hub_room_id="hub-room-id")
        schemas = tools.get_openai_tool_schemas(capabilities=frozenset())
        names = {s["function"]["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names)

    def test_default_include_contacts_true_in_normal_room(self, mock_rest) -> None:
        """Backward compat: contact tools included by default outside hub room."""
        tools = AgentTools("room-A", mock_rest)
        schemas = tools.get_anthropic_tool_schemas()
        names = {s["name"] for s in schemas}
        assert CONTACT_TOOL_NAMES.issubset(names)
