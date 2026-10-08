"""Tests for which Parlant tools are built and how they are described."""

from __future__ import annotations

from typing import get_args

import pytest

from band.core.memory_types import enum_values
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState
from band.core.types import AdapterFeatures, Capability
from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.tools import TASK_TOOL_NAMES, TOOL_MODELS, ListContactRequestsInput

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


class TestCreateParlantTools:
    """Tests for create_parlant_tools() function.

    Real parlant, not mocked: ``create_parlant_tools`` builds its tools with the
    real ``@p.tool`` decorator, which introspects each function's signature/
    docstring into a real ``Tool.parameters`` schema — the schema shape *is* what
    these tests verify, so faking the decorator would test the fake, not the
    integration.
    """

    def test_returns_list_of_tools(self):
        """Should return list of tool entries when Parlant is installed."""
        tools = create_parlant_tools()

        assert isinstance(tools, list)
        # Non-empty; specific tool names are verified in the next test.
        # Avoid hardcoded counts so adding/removing tools doesn't silently
        # break this assertion — the next test validates the exact contract.
        assert len(tools) > 0

    def test_returns_expected_tool_names(self):
        """Should return tools with expected names."""
        tools = create_parlant_tools()

        # Tools are ToolEntry objects with a .tool attribute containing the Tool
        tool_names = [t.tool.name for t in tools]
        assert "band_send_message" in tool_names
        assert "band_no_reply" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_create_chatroom" in tool_names
        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        assert "band_remove_contact" in tool_names
        assert "band_list_contact_requests" in tool_names
        assert "band_respond_contact_request" in tool_names
        assert "band_list_room_files" in tool_names
        assert "band_read_room_file" in tool_names
        assert "band_send_room_file" in tool_names

    def test_tools_have_descriptions(self):
        """Should have descriptions for all tools."""
        tools = create_parlant_tools()

        for entry in tools:
            assert entry.tool.description, f"Tool {entry.tool.name} has no description"

    def test_description_reflects_master_model_edit(self, monkeypatch):
        """A master model docstring edit must reach the Parlant tool description.

        Mutates the actual source (``TOOL_MODELS`` docstrings) rather than
        re-deriving the expected text through ``get_tool_description()`` — the
        function under test's own dependency — so this can't pass on a
        hand-written docstring that coincidentally matches today's master text.
        That's the regression this fix closes: Parlant tools used to hand-write
        their own docs instead of reading the master model at all.
        """
        for name, model in TOOL_MODELS.items():
            sentinel = f"SENTINEL DOCSTRING FOR {name}"
            monkeypatch.setattr(model, "__doc__", sentinel)

        tools = create_parlant_tools()
        checked = 0
        for entry in tools:
            if entry.tool.name not in TOOL_MODELS:
                continue
            checked += 1
            assert f"SENTINEL DOCSTRING FOR {entry.tool.name}" in entry.tool.description
        assert checked == len(tools), (
            "expected every Parlant tool to have a master model"
        )

    def test_tool_parameters_have_descriptions(self):
        """Every tool argument should carry a description, not just the tool itself.

        Parlant's schema builder never reads a docstring's Args: section (unlike
        pydantic-ai's griffe parser) — a parameter only gets a description via
        Annotated[T, ToolParameterOptions(description=...)] on its type
        annotation. Without that, every argument silently reaches the LLM with
        no description at all.
        """
        tools = create_parlant_tools()

        missing = [
            (entry.tool.name, param_name)
            for entry in tools
            for param_name, (_, options) in entry.tool.parameters.items()
            if not options.description
        ]
        assert not missing, f"parameters with no description: {missing}"

    def test_parameter_description_reflects_master_model_field_edit(self, monkeypatch):
        """A master field description edit must reach the Parlant parameter schema.

        Same mutation-test shape as test_description_reflects_master_model_edit,
        applied per argument instead of per tool. Scoped to parameters Parlant's
        tool functions actually accept — some master fields (e.g.
        AddParticipantInput.role, SendEventInput.metadata, both LookupPeersInput
        fields) aren't exposed as Parlant parameters at all; the tool hardcodes
        that value internally instead.
        """
        baseline_params = {
            (entry.tool.name, param_name)
            for entry in create_parlant_tools()
            for param_name in entry.tool.parameters
        }

        sentinels: dict[tuple[str, str], str] = {}
        for tool_name, model in TOOL_MODELS.items():
            for field_name, field in model.model_fields.items():
                if (
                    tool_name,
                    field_name,
                ) not in baseline_params or not field.description:
                    continue
                sentinel = f"SENTINEL FIELD DESC FOR {tool_name}.{field_name}"
                monkeypatch.setattr(field, "description", sentinel)
                sentinels[(tool_name, field_name)] = sentinel

        tools = create_parlant_tools()
        checked = 0
        for entry in tools:
            for param_name, (_, options) in entry.tool.parameters.items():
                sentinel = sentinels.get((entry.tool.name, param_name))
                if sentinel is None:
                    continue
                checked += 1
                assert options.description is not None
                assert options.description.startswith(sentinel)
        assert checked == len(sentinels), (
            "expected every field-described master parameter to reach Parlant"
        )

    def test_send_message_mentions_param_notes_comma_separated_shape(self):
        """mentions is a comma-separated string in Parlant, not the master's list[str].

        The per-argument description must say so, not just the tool-level docstring —
        otherwise an LLM asking about this one argument sees the master's
        list-oriented wording unqualified.
        """
        tools = create_parlant_tools()

        send_message_entry = next(
            t for t in tools if t.tool.name == "band_send_message"
        )
        description = send_message_entry.tool.parameters["mentions"][1].description

        assert description is not None
        assert "comma" in description

    def test_list_contact_requests_sent_status_description_lists_literal_choices(self):
        """sent_status's master field is Literal[...]; handing that type to
        Parlant directly crashes tool registration (Parlant's schema builder
        only turns a real enum.Enum into an ``enum``), so its choices must
        reach the LLM as prose in the description instead of vanishing.
        """
        choices = get_args(
            ListContactRequestsInput.model_fields["sent_status"].annotation
        )

        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.CONTACTS})
        )
        entry = next(t for t in tools if t.tool.name == "band_list_contact_requests")
        description = entry.tool.parameters["sent_status"][1].description

        assert description is not None
        for choice in choices:
            assert choice in description

    def test_send_message_tool_has_required_parameters(self):
        """send_message should have content and mentions parameters."""
        tools = create_parlant_tools()

        send_message_entry = next(
            t for t in tools if t.tool.name == "band_send_message"
        )
        # Parameters is a dict with param names as keys
        param_names = list(send_message_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "mentions" in param_names

    def test_send_event_tool_has_message_type_parameter(self):
        """send_event should have message_type parameter."""
        tools = create_parlant_tools()

        send_event_entry = next(t for t in tools if t.tool.name == "band_send_event")
        param_names = list(send_event_entry.tool.parameters.keys())

        assert "content" in param_names
        assert "message_type" in param_names

    def test_add_participant_tool_has_identifier_parameter(self):
        """add_participant should have identifier parameter."""
        tools = create_parlant_tools()

        add_participant_entry = next(
            t for t in tools if t.tool.name == "band_add_participant"
        )
        param_names = list(add_participant_entry.tool.parameters.keys())

        assert "identifier" in param_names

    def test_lookup_peers_has_no_parameters(self):
        """lookup_peers should have no user-facing parameters (pagination is hardcoded)."""
        tools = create_parlant_tools()

        lookup_peers_entry = next(
            t for t in tools if t.tool.name == "band_lookup_peers"
        )
        param_names = list(lookup_peers_entry.tool.parameters.keys())

        # Pagination was intentionally removed to simplify the API
        # The function uses hardcoded defaults (page=1, page_size=50)
        assert param_names == []

    def test_excludes_contact_tools_without_capability(self):
        """Contact tools excluded when CONTACTS capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [t.tool.name for t in tools]

        assert "band_send_message" in tool_names
        assert "band_create_chatroom" in tool_names
        assert "band_list_contacts" not in tool_names
        assert "band_add_contact" not in tool_names
        assert "band_remove_contact" not in tool_names
        assert "band_list_contact_requests" not in tool_names
        assert "band_respond_contact_request" not in tool_names

    def test_excludes_file_tools_without_capability(self):
        """File tools excluded when FILES capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" not in tool_names
        assert "band_read_room_file" not in tool_names
        assert "band_send_room_file" not in tool_names

    def test_includes_file_tools_with_capability(self):
        """File tools included when FILES capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.FILES})
        )
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" in tool_names
        assert "band_read_room_file" in tool_names
        assert "band_send_room_file" in tool_names

    def test_includes_file_tools_when_no_features(self):
        """File tools included when features is None (backward compat)."""
        tools = create_parlant_tools(features=None)
        tool_names = [t.tool.name for t in tools]

        assert "band_list_room_files" in tool_names
        assert "band_send_room_file" in tool_names

    def test_send_room_file_mentions_param_notes_comma_separated_shape(self):
        """mentions is a comma-separated string in Parlant, not the master's list[str]."""
        tools = create_parlant_tools()

        entry = next(t for t in tools if t.tool.name == "band_send_room_file")
        description = entry.tool.parameters["mentions"][1].description

        assert description is not None
        assert "comma" in description

    def test_read_room_file_tool_has_file_id_parameter(self):
        """read_room_file should have a file_id parameter."""
        tools = create_parlant_tools()

        entry = next(t for t in tools if t.tool.name == "band_read_room_file")
        param_names = list(entry.tool.parameters.keys())

        assert "file_id" in param_names

    def test_includes_contact_tools_with_capability(self):
        """Contact tools included when CONTACTS capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.CONTACTS})
        )
        tool_names = [t.tool.name for t in tools]

        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        assert "band_remove_contact" in tool_names
        assert "band_list_contact_requests" in tool_names
        assert "band_respond_contact_request" in tool_names

    def test_includes_contact_tools_when_no_features(self):
        """Contact tools included when features is None (backward compat)."""
        tools = create_parlant_tools(features=None)
        tool_names = [t.tool.name for t in tools]

        assert "band_list_contacts" in tool_names
        assert "band_respond_contact_request" in tool_names

    def test_excludes_task_tools_without_capability(self):
        """Task tools excluded when TASKS capability is absent."""
        tools = create_parlant_tools(features=AdapterFeatures())
        tool_names = {t.tool.name for t in tools}

        assert "band_send_message" in tool_names
        assert not TASK_TOOL_NAMES & tool_names

    def test_includes_task_tools_with_capability(self):
        """Task tools included when TASKS capability is present."""
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.TASKS})
        )
        tool_names = {t.tool.name for t in tools}

        assert TASK_TOOL_NAMES <= tool_names

    def test_update_task_status_and_state_are_real_enums(self):
        """status/state are real StrEnum fields, so Parlant renders them as a
        JSON-Schema enum directly -- no Literal-choices-in-prose fallback needed.
        """
        tools = create_parlant_tools(
            features=AdapterFeatures(capabilities={Capability.TASKS})
        )
        entry = next(t for t in tools if t.tool.name == "band_update_task")

        status_schema = entry.tool.parameters["status"][0]
        state_schema = entry.tool.parameters["state"][0]

        assert set(status_schema["enum"]) == set(enum_values(TaskAssignmentStatus))
        assert set(state_schema["enum"]) == set(enum_values(TaskLifecycleState))
