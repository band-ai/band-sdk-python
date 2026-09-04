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
    AddParticipantInput,
    GetTaskInput,
    LookupPeersInput,
    SendEventInput,
    SendMessageInput,
    SetBoardInput,
    UpdateTaskInput,
    format_arg_doc,
    get_tool_description,
    get_tool_docstring_with_args,
    platform_args_schema,
    platform_tool,
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

    @pytest.mark.parametrize("content", ["", "   ", "\n\t "])
    def test_rejects_content_with_no_visible_characters(self, content):
        """Content must have at least one visible character, not just be non-empty."""
        with pytest.raises(ValidationError) as exc_info:
            SendMessageInput(content=content, mentions=["Alice"])
        assert "content" in str(exc_info.value)


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

    @pytest.mark.parametrize("content", ["", "   ", "\n\t "])
    def test_rejects_content_with_no_visible_characters(self, content):
        """Content must have at least one visible character, not just be non-empty."""
        with pytest.raises(ValidationError) as exc_info:
            SendEventInput(content=content, message_type="thought")
        assert "content" in str(exc_info.value)


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


class TestUpdateTaskInput:
    """Tests for UpdateTaskInput model."""

    def test_requires_at_least_one_field_besides_id(self):
        """id alone is a no-op write; the model rejects it up front so an
        agent gets a clear error instead of the call silently doing nothing."""
        with pytest.raises(ValidationError, match="At least one of"):
            UpdateTaskInput(id="task-1")

    def test_one_field_besides_id_is_sufficient(self):
        update = UpdateTaskInput(id="task-1", comment="progress note")
        assert update.comment == "progress note"

    def test_explicit_empty_string_counts_as_set(self):
        """An explicit "" is a real value, not an omission -- must not trip
        the at-least-one-field check the same way an unset field does."""
        update = UpdateTaskInput(id="task-1", comment="")
        assert update.comment == ""


class TestSetBoardInput:
    """Tests for SetBoardInput model."""

    def test_requires_at_least_one_field(self):
        """No fields is a no-op write; the model rejects it up front so an
        agent gets a clear error instead of the call silently doing nothing."""
        with pytest.raises(ValidationError, match="At least one of"):
            SetBoardInput()

    def test_one_field_is_sufficient(self):
        board = SetBoardInput(goal_title="Ship v2")
        assert board.goal_title == "Ship v2"


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
            "band_list_room_files",
            "band_read_room_file",
            "band_send_room_file",
            "band_list_tasks",
            "band_create_task",
            "band_get_task",
            "band_update_task",
            "band_get_task_history",
            "band_get_board",
            "band_set_board",
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


def args_section(docstring: str) -> dict[str, str]:
    """Parse the rendered ``Args:`` block back into {arg_name: description}.

    Mirrors how a Google-style docstring parser reads it: a line indented past
    the argument name continues the previous entry.
    """
    _, _, args_block = docstring.partition("\nArgs:\n")
    entries: dict[str, str] = {}
    current = ""
    for line in args_block.splitlines():
        if not line.strip():
            continue
        if line.startswith("        "):
            entries[current] += " " + line.strip()
            continue
        current, _, description = line.strip().partition(": ")
        entries[current] = description
    return entries


class TestGetToolDocstringWithArgs:
    """The Args: section is rendered from the master fields, never hand-written."""

    @pytest.mark.parametrize(
        "name", ["band_send_message", "band_add_participant", "band_store_memory"]
    )
    def test_args_section_mirrors_master_field_descriptions(self, name):
        model = TOOL_MODELS[name]
        expected = {
            field_name: field.description
            for field_name, field in model.model_fields.items()
            if field.description
        }

        assert args_section(get_tool_docstring_with_args(name)) == expected

    def test_description_comes_first_verbatim(self):
        docstring = get_tool_docstring_with_args("band_send_message")

        assert docstring.startswith(get_tool_description("band_send_message").rstrip())

    def test_fieldless_model_gets_no_args_section(self):
        """GetParticipantsInput has no fields, so there is nothing to document."""
        assert get_tool_docstring_with_args("band_get_participants") == (
            get_tool_description("band_get_participants")
        )

    def test_multiline_description_stays_one_entry(self):
        """A flush-left continuation would end the entry for a docstring parser."""
        rendered = format_arg_doc("mentions", "first line\nsecond line")

        assert rendered == "    mentions: first line\n        second line"
        assert args_section(f"x\n\nArgs:\n{rendered}") == {
            "mentions": "first line second line"
        }

    def test_whitespace_only_description_is_omitted(self, monkeypatch):
        """A whitespace-only Field(description=...) must be filtered out.

        It's truthy, so without filtering it reaches format_arg_doc's
        splitlines unpack — that unpack has no floor and raises ValueError on
        an empty result, which would crash any adapter importing this tool's
        schema.
        """
        from pydantic import BaseModel, Field

        class ProbeInput(BaseModel):
            """Probe tool for a whitespace-only field description."""

            blank: str = Field(..., description="   ")
            real: str = Field(..., description="Has text")

        monkeypatch.setitem(TOOL_MODELS, "band_probe_whitespace", ProbeInput)

        docstring = get_tool_docstring_with_args("band_probe_whitespace")

        assert args_section(docstring) == {"real": "Has text"}


class TestPlatformTool:
    def test_decorator_applies_master_docstring(self):
        @platform_tool
        def band_add_participant(identifier: str, role: str) -> None: ...

        assert band_add_participant.__doc__ == get_tool_docstring_with_args(
            "band_add_participant"
        )


class TestPlatformArgsSchema:
    def test_subclasses_master_model_unchanged_without_validators(self):
        """Not the same object as the master model (it's always wrapped to
        sanitize its schema -- see the const/enum test below), but a subclass
        that keeps the master's fields, docstring, and validation behavior."""
        schema = platform_args_schema("band_send_message")

        assert schema is not SendMessageInput
        assert issubclass(schema, SendMessageInput)
        assert schema.__doc__ == SendMessageInput.__doc__
        assert schema.model_fields.keys() == SendMessageInput.model_fields.keys()
        assert (
            schema(content="hi", mentions=["@alice"]).model_dump()
            == SendMessageInput(content="hi", mentions=["@alice"]).model_dump()
        )

    def test_sanitizes_single_value_literal_to_enum(self):
        """A single-value Literal field renders as JSON-Schema `const` by
        default (a Pydantic quirk); some providers' restricted JSON-Schema
        subsets (e.g. Gemini) reject `const`, so every args_schema must widen
        it to `enum` before reaching a framework -- not just the master
        model/MCP paths that already call sanitize_tool_schema() directly."""
        schema = platform_args_schema("band_get_task")

        assert "const" not in str(schema.model_json_schema())
        assert schema.model_json_schema()["properties"]["include"]["anyOf"][0][
            "enum"
        ] == ["history"]

    def test_get_task_input_still_declares_const_before_sanitizing(self):
        """Guards the premise of the test above: if Pydantic ever stops
        emitting `const` for this field shape, the sanitize path would be
        exercising nothing."""
        assert (
            GetTaskInput.model_json_schema()["properties"]["include"]["anyOf"][0][
                "const"
            ]
            == "history"
        )

    def test_subclass_keeps_master_description_and_field_text(self):
        from pydantic import field_validator

        schema = platform_args_schema(
            "band_send_message",
            validators={
                "coerce": field_validator("mentions", mode="before")(
                    staticmethod(lambda v: [v] if isinstance(v, str) else v)
                )
            },
        )

        assert issubclass(schema, SendMessageInput)
        assert schema.__doc__ == SendMessageInput.__doc__
        assert (
            schema.model_fields["mentions"].description
            == SendMessageInput.model_fields["mentions"].description
        )
        assert schema(content="hi", mentions="@alice").mentions == ["@alice"]
