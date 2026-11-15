"""ToolSetComposition behavior at the CrewAI tool boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from band.core.exceptions import BandToolError
from band.core.memory_types import (
    MemorySegment,
    MemorySystem,
    MemoryType,
)
from band.core.types import AdapterFeatures, Capability, Emit
from band.runtime.custom_tools import get_custom_tool_name
from tests.crewaicontract import tool_result_data
from tests.integrations.crewaitools.kit import (
    _registry_names,
    _unique_names,
)


class TestToolSetComposition:
    @pytest.mark.parametrize(
        "capabilities",
        [
            frozenset(),
            frozenset({Capability.CONTACTS}),
            frozenset({Capability.MEMORY}),
            frozenset({Capability.FILES}),
            frozenset({Capability.CONTACTS, Capability.MEMORY}),
            frozenset({Capability.CONTACTS, Capability.MEMORY, Capability.FILES}),
        ],
        ids=lambda caps: "+".join(sorted(caps)) or "base",
    )
    def test_tool_surface_is_the_registry_surface(self, builder_mod, capabilities):
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=capabilities,
        )
        assert _unique_names(tools) == _registry_names(capabilities)

    def test_custom_tools_appended(self, builder_mod):

        class MyInput(BaseModel):
            """My custom tool."""

            value: str

        async def my_handler(_: MyInput) -> str:
            return "ok"

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
            custom_tools=[(MyInput, my_handler)],
        )
        assert _unique_names(tools) == _registry_names(frozenset()) | {
            get_custom_tool_name(MyInput)
        }

    def test_adapter_feature_filters_apply_to_platform_tools(self, builder_mod):

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=("contacts", "memory"),
                exclude_tools=("band_remove_contact", "band_archive_memory"),
            ),
        )

        names = {t.name for t in tools}
        assert "band_send_message" not in names
        assert "band_list_contacts" in names
        assert "band_list_memories" in names
        assert "band_remove_contact" not in names
        assert "band_archive_memory" not in names

    def test_adapter_feature_filters_only_apply_to_platform_tools(self, builder_mod):

        class MyInput(BaseModel):
            value: str

        class OtherInput(BaseModel):
            value: str

        async def handler(_: BaseModel) -> str:
            return "ok"

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            features=AdapterFeatures(
                include_tools=("band_send_message", "myinput"),
                exclude_tools=("myinput",),
            ),
            custom_tools=[(MyInput, handler), (OtherInput, handler)],
        )

        names = {t.name for t in tools}
        assert names == {"band_send_message", "my", "other"}

    @pytest.mark.parametrize(
        ("tool_name", "payload"),
        [
            ("band_send_event", {"content": "thinking", "message_type": "debug"}),
            ("band_add_participant", {"identifier": "peer", "role": "viewer"}),
            ("band_lookup_peers", {"page_size": 101}),
            ("band_list_contacts", {"page": 0}),
            ("band_list_contact_requests", {"sent_status": "done"}),
            ("band_respond_contact_request", {"action": "maybe"}),
            ("band_list_memories", {"type": "fact"}),
            (
                "band_store_memory",
                {
                    "content": "remember this",
                    "system": "working",
                    "type": "fact",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "organization",
                },
            ),
        ],
    )
    def test_platform_tool_schemas_reject_invalid_values(
        self, platform_args_schemas, tool_name, payload
    ):

        with pytest.raises(ValidationError):
            platform_args_schemas[tool_name].model_validate(payload)

    def test_platform_tool_schemas_accept_metadata_fields(self, platform_args_schemas):
        assert platform_args_schemas["band_send_event"].model_validate(
            {
                "content": "state update",
                "message_type": "task",
                "metadata": {"run_id": "run-1"},
            }
        ).metadata == {"run_id": "run-1"}
        assert platform_args_schemas["band_store_memory"].model_validate(
            {
                "content": "remember this",
                "system": MemorySystem.WORKING,
                "type": MemoryType.SEMANTIC,
                "segment": MemorySegment.USER,
                "thought": "useful later",
                "scope": "organization",
                "metadata": {"source": "crewai"},
            }
        ).metadata == {"source": "crewai"}

    def test_lookup_peers_forwards_pagination(self, builder_mod):
        tools_obj = MagicMock()
        tools_obj.lookup_peers = AsyncMock(
            return_value={"peers": [], "metadata": {"page": 2, "page_size": 25}}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        lookup_peers = next(t for t in tools if t.name == "band_lookup_peers")

        result = tool_result_data(lookup_peers._run(page=2, page_size=25))

        assert result["status"] == "success"
        tools_obj.lookup_peers.assert_awaited_once_with(2, 25)

    def test_lookup_peers_reports_serialized_result_for_raw_model_return(
        self, builder_mod
    ):
        """A raw platform model still produces an observable tool result."""

        class FakePeersResponse(BaseModel):
            peers: list[dict[str, str]]

        tools_obj = MagicMock()
        tools_obj.lookup_peers = AsyncMock(
            return_value=FakePeersResponse(peers=[{"id": "p1"}])
        )
        tools_obj.send_event = AsyncMock()
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.EmitToolCallsReporter(features),
            capabilities=frozenset(),
        )
        lookup_peers = next(t for t in tools if t.name == "band_lookup_peers")

        result = tool_result_data(lookup_peers._run())

        assert result["status"] == "success"
        assert result["peers"] == [{"id": "p1"}]
        result_event = tools_obj.send_event.call_args_list[-1]
        assert result_event.kwargs["message_type"] == "tool_result"
        reported = tool_result_data(result_event.kwargs["content"])
        assert reported["output"] == {"peers": [{"id": "p1"}]}
        assert reported["is_error"] is False

    def test_send_failure_appends_available_handles(self, builder_mod):
        """The real empty-mentions error already lists the room's handles, so the
        CrewAI enricher must surface them once — not append a second copy."""

        tools_obj = MagicMock()
        tools_obj.agent_id = None
        # The actual error AgentTools.send_message raises: it already carries the
        # "Available handles:" hint.
        tools_obj.send_message = AsyncMock(
            side_effect=BandToolError(
                "At least one mention is required. "
                "Available handles: ['@john', '@john/weather-agent']. "
                "Use participant handles from the list."
            )
        )
        tools_obj.participants = [
            {"id": "1", "name": "John", "handle": "@john"},
            {"id": "2", "name": "Weather", "handle": "@john/weather-agent"},
            {"id": "3", "name": "No Handle"},
        ]
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message = next(t for t in tools if t.name == "band_send_message")

        result = tool_result_data(send_message._run(content="hello", mentions=[]))

        assert result["status"] == "error"
        assert "@john" in result["message"]
        assert "@john/weather-agent" in result["message"]
        # Participants without a handle are not offered as mention options.
        assert "No Handle" not in result["message"]
        # The enricher is idempotent: the handle list is not duplicated.
        assert result["message"].count("Available handles:") == 1

    def test_send_failure_excludes_agent_own_handle(self, builder_mod):
        """The agent's own handle is never offered as a retry option — an
        agent can't @mention itself, so listing it only misleads the LLM."""

        tools_obj = MagicMock()
        tools_obj.agent_id = "self-2"
        # A failure that does not already carry handles, so the enricher computes
        # the available options itself and must exclude the agent's own handle.
        tools_obj.send_message = AsyncMock(
            side_effect=BandToolError("Failed to deliver message")
        )
        tools_obj.participants = [
            {"id": "1", "name": "John", "handle": "@john"},
            {"id": "self-2", "name": "Me", "handle": "@john/weather-agent"},
        ]
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message = next(t for t in tools if t.name == "band_send_message")

        result = tool_result_data(send_message._run(content="hello", mentions=[]))

        assert result["status"] == "error"
        assert "@john" in result["message"]
        # The agent's own handle is excluded from the available options.
        assert "@john/weather-agent" not in result["message"]
