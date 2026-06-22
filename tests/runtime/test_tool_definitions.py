"""
Unit tests for tool_definitions - Pydantic models for platform tools.

Tests:
1. Pydantic models validate correctly
2. Required fields are enforced
3. Enum constraints work
4. JSON schema generation is correct
"""

import pytest
from pydantic import ValidationError

from band.runtime.tools import (
    TOOL_MODELS,
    SendMessageInput,
    SendEventInput,
    AddParticipantInput,
    LookupPeersInput,
    get_tool_description,
)


class TestSendMessageInput:
    """Tests for SendMessageInput model."""

    def test_valid_message(self):
        """Valid message with content and mentions should pass."""
        msg = SendMessageInput(content="Hello", mentions=["Alice"])
        assert msg.content == "Hello"
        assert msg.mentions == ["Alice"]

    def test_requires_content(self):
        """Content is required."""
        with pytest.raises(ValidationError) as exc_info:
            SendMessageInput(mentions=["Alice"])
        assert "content" in str(exc_info.value)

    def test_requires_mentions(self):
        """Mentions is required."""
        with pytest.raises(ValidationError) as exc_info:
            SendMessageInput(content="Hello")
        assert "mentions" in str(exc_info.value)

    def test_mentions_accepts_empty_list(self):
        """Empty mentions pass Pydantic validation (runtime validates instead)."""
        model = SendMessageInput(content="Hello", mentions=[])
        assert model.mentions == []


class TestSendEventInput:
    """Tests for SendEventInput model."""

    def test_valid_event(self):
        """Valid event with all fields should pass."""
        event = SendEventInput(
            content="Processing...",
            message_type="thought",
            metadata={"step": 1},
        )
        assert event.content == "Processing..."
        assert event.message_type == "thought"
        assert event.metadata == {"step": 1}

    def test_message_type_enum(self):
        """message_type must be one of the allowed values."""
        # Valid values
        for valid_type in ["thought", "error", "task"]:
            event = SendEventInput(content="Test", message_type=valid_type)
            assert event.message_type == valid_type

        # Invalid value
        with pytest.raises(ValidationError) as exc_info:
            SendEventInput(content="Test", message_type="invalid")
        assert "message_type" in str(exc_info.value)

    def test_metadata_optional(self):
        """Metadata should be optional."""
        event = SendEventInput(content="Test", message_type="thought")
        assert event.metadata is None


class TestAddParticipantInput:
    """Tests for AddParticipantInput model."""

    def test_valid_add(self):
        """Valid add with identifier should pass."""
        add = AddParticipantInput(identifier="Bob")
        assert add.identifier == "Bob"
        assert add.role == "member"  # default

    def test_role_enum(self):
        """role must be one of the allowed values."""
        for valid_role in ["owner", "admin", "member"]:
            add = AddParticipantInput(identifier="Bob", role=valid_role)
            assert add.role == valid_role

        with pytest.raises(ValidationError):
            AddParticipantInput(identifier="Bob", role="invalid")


class TestLookupPeersInput:
    """Tests for LookupPeersInput model."""

    def test_defaults(self):
        """Default values should be applied."""
        lookup = LookupPeersInput()
        assert lookup.page == 1
        assert lookup.page_size == 50

    def test_page_size_max(self):
        """page_size should have max constraint."""
        with pytest.raises(ValidationError):
            LookupPeersInput(page_size=101)


class TestToolModelsRegistry:
    """Tests for the TOOL_MODELS registry."""

    def test_all_tools_registered(self):
        """All expected tools should be in the registry."""
        expected = {
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_lookup_peers",
            "band_get_participants",
            "band_create_chatroom",
            "band_list_contacts",
            "band_add_contact",
            "band_remove_contact",
            "band_list_contact_requests",
            "band_respond_contact_request",
            "band_list_memories",
            "band_store_memory",
            "band_get_memory",
            "band_supersede_memory",
            "band_archive_memory",
        }
        assert set(TOOL_MODELS.keys()) == expected

    def test_models_have_docstrings(self):
        """All models should have docstrings for LLM descriptions."""
        for name, model in TOOL_MODELS.items():
            assert model.__doc__, f"{name} should have a docstring"

    def test_json_schema_generation(self):
        """All models should generate valid JSON schemas."""
        for name, model in TOOL_MODELS.items():
            schema = model.model_json_schema()
            assert "properties" in schema or "type" in schema, (
                f"{name} should generate valid schema"
            )


class TestGetToolDescription:
    """Tests for get_tool_description function."""

    def test_returns_description_for_prefixed_name(self):
        """Should return description for prefixed tool name."""
        desc = get_tool_description("band_send_message")
        assert desc is not None
        assert len(desc) > 0
        assert "Execute" not in desc  # Should be real description, not fallback

    def test_deprecation_warning_for_unprefixed_name(self):
        """Should emit deprecation warning for unprefixed tool name."""
        with pytest.warns(DeprecationWarning, match="send_message.*deprecated"):
            desc = get_tool_description("send_message")

        # Should still return the description
        assert desc is not None
        assert len(desc) > 0

    def test_fallback_for_unknown_tool(self):
        """Should return fallback for unknown tool name."""
        desc = get_tool_description("unknown_tool")
        assert desc == "Execute unknown_tool"
