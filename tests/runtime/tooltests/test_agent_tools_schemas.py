from __future__ import annotations

from unittest.mock import MagicMock

from band.core.types import Capability
from band.runtime.tools import TOOL_MODELS, AgentTools


class TestAgentToolsSchemas:
    """Test tool schema generation."""

    def test_tool_models_registry(self) -> None:
        """TOOL_MODELS should contain all tool input models."""
        assert "band_send_message" in TOOL_MODELS
        assert "band_send_event" in TOOL_MODELS
        assert "band_add_participant" in TOOL_MODELS
        assert "band_remove_participant" in TOOL_MODELS
        assert "band_lookup_peers" in TOOL_MODELS
        assert "band_get_participants" in TOOL_MODELS
        assert "band_create_chatroom" in TOOL_MODELS

    def test_tool_models_property(self, mock_rest_client: MagicMock) -> None:
        """tool_models property should return registry."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.tool_models is TOOL_MODELS

    def test_get_tool_schemas_openai(self, mock_rest_client: MagicMock) -> None:
        """get_tool_schemas('openai') should return OpenAI format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("openai")

        tool_names = [s["function"]["name"] for s in schemas]
        # Base platform tools
        assert "band_send_message" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_create_chatroom" in tool_names
        # Contact tools included by default
        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        # Memory tools excluded by default
        assert "band_list_memories" not in tool_names
        assert "band_store_memory" not in tool_names

        send_msg = next(
            s for s in schemas if s["function"]["name"] == "band_send_message"
        )
        assert send_msg["type"] == "function"
        assert "parameters" in send_msg["function"]
        assert "description" in send_msg["function"]

    def test_get_tool_schemas_openai_with_memory(
        self, mock_rest_client: MagicMock
    ) -> None:
        """get_tool_schemas('openai', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "openai", capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS})
        )

        tool_names = [s["function"]["name"] for s in schemas]
        # Memory tools present
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_get_memory" in tool_names
        assert "band_supersede_memory" in tool_names
        assert "band_archive_memory" in tool_names
        # Base and contact tools still present
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names

    def test_get_tool_schemas_anthropic(self, mock_rest_client: MagicMock) -> None:
        """get_tool_schemas('anthropic') should return Anthropic format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("anthropic")

        tool_names = [s["name"] for s in schemas]
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names
        assert "band_list_memories" not in tool_names

        send_msg = next(s for s in schemas if s["name"] == "band_send_message")
        assert "input_schema" in send_msg
        assert "description" in send_msg

    def test_get_tool_schemas_anthropic_with_memory(
        self, mock_rest_client: MagicMock
    ) -> None:
        """get_tool_schemas('anthropic', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "anthropic",
            capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS}),
        )

        tool_names = [s["name"] for s in schemas]
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names

    def test_schemas_drop_numeric_bounds(self, mock_rest_client: MagicMock) -> None:
        """Pydantic Field(ge=.., le=..) renders minimum/maximum, which some
        providers reject on integer params; the schemas must omit them while the
        models still enforce the bounds at execution."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "openai", capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS})
        )

        page_size = next(
            s["function"]["parameters"]["properties"]["page_size"]
            for s in schemas
            if s["function"]["name"] == "band_lookup_peers"
        )
        assert "minimum" not in page_size
        assert "maximum" not in page_size
        assert page_size["type"] == "integer"
