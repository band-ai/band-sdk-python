"""Tests for the shared CrewAI tool builder in band.integrations.crewai.

These tests cover the extracted surface (build_band_crewai_tools, the
reporter implementations, and run_async behavior) without going through
either CrewAIAdapter or CrewAIFlowAdapter — the builder is the seam they
both consume.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


class MockBaseTool:
    """Minimal stand-in for crewai.tools.BaseTool at import time."""

    name: str = ""
    description: str = ""

    def __init__(self) -> None:
        pass


@pytest.fixture
def crewai_mocks(monkeypatch):
    mock_crewai_tools_module = MagicMock()
    mock_crewai_tools_module.BaseTool = MockBaseTool
    mock_nest_asyncio = MagicMock()

    # `_nest_asyncio_applied` is process-global. Any test running with a mocked
    # nest_asyncio can flip it True without anything actually being patched, which
    # then silently disables the real patch for every later test — so isolate it.
    runtime = importlib.import_module("band.integrations.crewai.runtime")
    monkeypatch.setattr(runtime, "_nest_asyncio_applied", False)

    # No sys.modules surgery: every crewai import in the band modules is
    # TYPE_CHECKING-only or function-local, so they pick the mocks up at call time.
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    yield mock_nest_asyncio


@pytest.fixture
def builder_mod(crewai_mocks):
    import importlib

    return importlib.import_module("band.integrations.crewai.tools")


@pytest.fixture
def runtime_mod(crewai_mocks):
    import importlib

    return importlib.import_module("band.integrations.crewai.runtime")


@pytest.fixture
def platform_args_schemas(builder_mod):
    """Tool name -> the args schema CrewAI actually advertises to the LLM."""
    from band.core.types import Capability

    tools = builder_mod.build_band_crewai_tools(
        get_context=lambda: None,
        reporter=builder_mod.NoopReporter(),
        capabilities=frozenset(
            {Capability.CONTACTS, Capability.MEMORY, Capability.FILES}
        ),
    )
    return {tool.name: tool.args_schema for tool in tools}


# --- Tool-set composition ---


class TestToolSetComposition:
    def test_base_tools_only(self, builder_mod):
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        names = {t.name for t in tools}
        assert names == {
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_get_participants",
            "band_lookup_peers",
            "band_create_chatroom",
        }
        assert len(tools) == 7

    def test_capability_contacts_adds_five(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS}),
        )
        names = {t.name for t in tools}
        contact_names = {
            "band_list_contacts",
            "band_add_contact",
            "band_remove_contact",
            "band_list_contact_requests",
            "band_respond_contact_request",
        }
        assert contact_names.issubset(names)
        assert len(tools) == 12

    def test_capability_memory_adds_five(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.MEMORY}),
        )
        names = {t.name for t in tools}
        memory_names = {
            "band_list_memories",
            "band_store_memory",
            "band_get_memory",
            "band_supersede_memory",
            "band_archive_memory",
        }
        assert memory_names.issubset(names)
        assert len(tools) == 12

    def test_capability_files_adds_three(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        names = {t.name for t in tools}
        file_names = {
            "band_list_room_files",
            "band_read_room_file",
            "band_send_room_file",
        }
        assert file_names.issubset(names)
        assert len(tools) == 10

    def test_both_capabilities(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
        )
        assert len(tools) == 17  # 7 base + 5 contacts + 5 memory

    def test_all_three_capabilities(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(
                {Capability.CONTACTS, Capability.MEMORY, Capability.FILES}
            ),
        )
        assert len(tools) == 20  # 7 base + 5 contacts + 5 memory + 3 files

    def test_custom_tools_appended(self, builder_mod):
        from pydantic import BaseModel

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
        # Custom tool name comes from the InputModel class name (lowercased)
        assert len(tools) == 8

    def test_adapter_feature_filters_apply_to_platform_tools(self, builder_mod):
        from band.core.types import AdapterFeatures, Capability

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
        from pydantic import BaseModel

        from band.core.types import AdapterFeatures

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
        from pydantic import ValidationError

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
                "system": "working",
                "type": "semantic",
                "segment": "user",
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

        result = json.loads(lookup_peers._run(page=2, page_size=25))

        assert result["status"] == "success"
        tools_obj.lookup_peers.assert_awaited_once_with(2, 25)

    def test_lookup_peers_reports_serialized_result_for_raw_model_return(
        self, builder_mod
    ):
        """lookup_peers (and the other six read-only tools that call
        call.tools.X directly, bypassing execute_tool_call's own
        serialization boundary) must still emit a tool_result event when the
        platform method returns a raw Pydantic/Fern model. report_result's
        json.dumps has no default=str, so an unserialized model previously
        raised inside report_result -- caught by its own try/except and only
        logged as a warning -- silently dropping the tool_result event."""
        from band.core.types import AdapterFeatures, Emit

        class FakePeersResponse:
            def __init__(self, data):
                self._data = data

            def model_dump(self):
                return self._data

        tools_obj = MagicMock()
        tools_obj.lookup_peers = AsyncMock(
            return_value=FakePeersResponse({"peers": [{"id": "p1"}]})
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

        result = json.loads(lookup_peers._run())

        assert result["status"] == "success"
        assert result["peers"] == [{"id": "p1"}]
        result_event = tools_obj.send_event.call_args_list[-1]
        assert result_event.kwargs["message_type"] == "tool_result"
        reported = json.loads(result_event.kwargs["content"])
        assert reported["output"] == {"peers": [{"id": "p1"}]}
        assert reported["is_error"] is False

    def test_send_message_marks_reply_tracker(self, builder_mod):
        """A successful band_send_message flips both ReplyTracker markers so the
        adapter can treat a later empty final answer as benign."""
        tools_obj = MagicMock()
        tools_obj.send_message = AsyncMock(return_value={"status": "sent"})
        tracker = builder_mod.ReplyTracker()
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=tools_obj, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message = next(t for t in tools if t.name == "band_send_message")

        result = json.loads(send_message._run(content="hello", mentions=[]))

        assert result["status"] == "success"
        tools_obj.send_message.assert_awaited_once()
        assert tracker.replied is True
        assert tracker.tool_executed is True

    def test_send_event_is_not_terminal_work(self, builder_mod):
        """band_send_event emits an observational event (thought/error/task), not
        terminal work — it must NOT flip tool_executed. So a turn that only sends an
        event and then yields an empty final answer is a genuine no-response failure
        the adapter still surfaces, not benign noise (see is_terminal_success)."""
        tools_obj = MagicMock()
        tools_obj.send_event = AsyncMock(return_value=None)
        tracker = builder_mod.ReplyTracker()
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=tools_obj, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_event = next(t for t in tools if t.name == "band_send_event")

        result = json.loads(send_event._run(content="thinking", message_type="task"))

        assert result["status"] == "success"
        assert tracker.tool_executed is False
        assert tracker.replied is False

    def test_read_only_tool_does_not_mark_tool_executed(self, builder_mod):
        """A successful read-only tool (lookup/listing) must NOT flip either
        marker. Fetching state is not a terminal action, so a turn that runs only
        a lookup and then yields an empty final answer is a genuine no-response
        failure the adapter must still surface — not benign noise."""
        tools_obj = MagicMock()
        tools_obj.lookup_peers = AsyncMock(return_value={"peers": []})
        tracker = builder_mod.ReplyTracker()
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=tools_obj, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        lookup_peers = next(t for t in tools if t.name == "band_lookup_peers")

        result = json.loads(lookup_peers._run())

        assert result["status"] == "success"
        assert tracker.tool_executed is False
        assert tracker.replied is False

    def test_reply_tracker_not_marked_on_send_failure(self, builder_mod):
        """A failed send must NOT mark either tracker — the turn produced nothing."""
        tools_obj = MagicMock()
        tools_obj.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        tracker = builder_mod.ReplyTracker()
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=tools_obj, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message = next(t for t in tools if t.name == "band_send_message")

        result = json.loads(send_message._run(content="hello", mentions=[]))

        assert result["status"] == "error"
        assert tracker.replied is False
        assert tracker.tool_executed is False

    def test_send_failure_appends_available_handles(self, builder_mod):
        """The real empty-mentions error already lists the room's handles, so the
        CrewAI enricher must surface them once — not append a second copy."""
        from band.core.exceptions import BandToolError

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

        result = json.loads(send_message._run(content="hello", mentions=[]))

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
        from band.core.exceptions import BandToolError

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

        result = json.loads(send_message._run(content="hello", mentions=[]))

        assert result["status"] == "error"
        assert "@john" in result["message"]
        # The agent's own handle is excluded from the available options.
        assert "@john/weather-agent" not in result["message"]


# --- File tools ---


class TestFileTools:
    def test_list_room_files_forwards_cursor(self, builder_mod):
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.list_room_files = AsyncMock(
            return_value={"data": [{"id": "file-1"}], "next_cursor": None}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        list_room_files = next(t for t in tools if t.name == "band_list_room_files")

        result = json.loads(list_room_files._run(cursor="cursor-1"))

        assert result["status"] == "success"
        tools_obj.list_room_files.assert_awaited_once_with("cursor-1")

    def test_list_room_files_default_cursor_is_none(self, builder_mod):
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.list_room_files = AsyncMock(return_value={"data": []})
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        list_room_files = next(t for t in tools if t.name == "band_list_room_files")

        list_room_files._run()

        tools_obj.list_room_files.assert_awaited_once_with(None)

    def test_read_room_file_forwards_file_id(self, builder_mod):
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(
            return_value={"name": "report.txt", "text": "hello"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = json.loads(read_room_file._run(file_id="file-1"))

        assert result["status"] == "success"
        tools_obj.read_room_file.assert_awaited_once_with("file-1")

    def test_read_room_file_image_result_becomes_vision_sentinel(self, builder_mod):
        """CrewAI's own StepExecutor rewrites a VISION_IMAGE:<media_type>:<b64>
        tool-result string into a real image_url content block -- pin that
        band_read_room_file emits exactly that sentinel for an image result."""
        from band.core.types import Capability

        image_result = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(return_value=image_result)
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = read_room_file._run(file_id="file-1")

        assert result == builder_mod.vision_sentinel(image_result)

    def test_read_room_file_image_result_reports_placeholder_not_base64(
        self, builder_mod
    ):
        """The full base64 sentinel must reach CrewAI's StepExecutor, but the
        platform tool_result event must not carry that same base64 blob."""
        from band.core.types import AdapterFeatures, Capability, Emit
        from band.runtime.tools import image_block_placeholder

        image_result = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        tools_obj = MagicMock()
        tools_obj.read_room_file = AsyncMock(return_value=image_result)
        tools_obj.send_event = AsyncMock()
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        reporter = builder_mod.EmitToolCallsReporter(
            AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=reporter,
            capabilities=frozenset({Capability.FILES}),
        )
        read_room_file = next(t for t in tools if t.name == "band_read_room_file")

        result = read_room_file._run(file_id="file-1")

        assert result == builder_mod.vision_sentinel(image_result)
        result_event = tools_obj.send_event.call_args_list[-1].kwargs
        reported_output = json.loads(result_event["content"])["output"]
        assert reported_output == image_block_placeholder(1)
        assert "ZmFrZQ==" not in reported_output

    def test_send_room_file_forwards_args_in_protocol_order(self, builder_mod):
        """AgentToolsProtocol.send_room_file wants (content, filename, caption,
        mentions) positionally -- pin the reorder from the tool's own kwargs."""
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {"id": "file-2"}, "message_id": "msg-1"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        result = json.loads(
            send_room_file._run(
                content="file body",
                filename="notes.txt",
                mentions=["Alice", "Bob"],
                caption="here's a file",
            )
        )

        assert result["status"] == "success"
        tools_obj.send_room_file.assert_awaited_once_with(
            "file body", "notes.txt", "here's a file", ["Alice", "Bob"]
        )

    def test_send_room_file_mentions_accepts_lenient_string_shape(self, builder_mod):
        """Smaller models emit mentions as a JSON-string or bracketed string,
        same leniency need as band_send_message -- see normalize_mentions_lenient."""
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {}, "message_id": "msg-1"}
        )
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        send_room_file._run(
            content="body", filename="notes.txt", mentions="@alice, @bob"
        )

        tools_obj.send_room_file.assert_awaited_once_with(
            "body", "notes.txt", "", ["@alice", "@bob"]
        )

    def test_send_room_file_reports_content_placeholder_not_raw_bytes(
        self, builder_mod
    ):
        """The full content must still reach send_room_file, but the
        tool_call event must not carry that same raw payload."""
        from band.core.types import AdapterFeatures, Capability, Emit
        from band.runtime.tools import file_content_placeholder

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(
            return_value={"attachment": {"id": "file-2"}, "message_id": "msg-1"}
        )
        tools_obj.send_event = AsyncMock()
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        reporter = builder_mod.EmitToolCallsReporter(
            AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=reporter,
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        # Multi-byte characters pin that the placeholder reports UTF-8 byte
        # length (what MAX_SEND_CONTENT_BYTES actually measures), not
        # len(content)'s character count.
        content = "raw file body 你好 " * 1000
        send_room_file._run(content=content, filename="notes.txt", mentions=["Alice"])

        tools_obj.send_room_file.assert_awaited_once_with(
            content, "notes.txt", "", ["Alice"]
        )
        call_event = tools_obj.send_event.call_args_list[0].kwargs
        reported_args = json.loads(call_event["content"])["args"]
        assert reported_args["content"] == file_content_placeholder(
            len(content.encode("utf-8"))
        )
        assert content not in json.dumps(reported_args)

    def test_send_room_file_failure_returns_error_status(self, builder_mod):
        from band.core.types import Capability

        tools_obj = MagicMock()
        tools_obj.send_room_file = AsyncMock(side_effect=RuntimeError("upload failed"))
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools_obj)
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )
        send_room_file = next(t for t in tools if t.name == "band_send_room_file")

        result = json.loads(
            send_room_file._run(
                content="body", filename="notes.txt", mentions=["Alice"]
            )
        )

        assert result["status"] == "error"
        assert "upload failed" in result["message"]


# --- Reporter behavior ---


class TestEmitToolCallsReporter:
    @pytest.mark.asyncio
    async def test_does_not_emit_when_tool_calls_unset(self, builder_mod):
        from band.core.types import AdapterFeatures

        features = AdapterFeatures()  # empty emit set
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_tool_calls_set(self, builder_mod):
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        assert tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_emits_canonical_event_schema(self, builder_mod):
        """The emitted payloads must use the canonical name/args/output schema.

        Every framework's tool_call / tool_result events are read back through
        the shared ``parse_tool_call`` / ``parse_tool_result`` (and the E2E
        observer), which key off ``name`` / ``args`` / ``output``. crewai once
        emitted ``tool`` / ``input`` / ``result`` instead, so its tool events
        were silently dropped on read. Pin the schema here so a count-only
        assertion can't let that drift back in.
        """
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "lookup", {"key": "alpha"})
        await reporter.report_result(tools, "lookup", "SECRET-123")

        call_kwargs, result_kwargs = (c.kwargs for c in tools.send_event.call_args_list)

        assert call_kwargs["message_type"] == "tool_call"
        assert json.loads(call_kwargs["content"]) == {
            "name": "lookup",
            "args": {"key": "alpha"},
        }

        assert result_kwargs["message_type"] == "tool_result"
        assert json.loads(result_kwargs["content"]) == {
            "name": "lookup",
            "output": "SECRET-123",
            "is_error": False,
        }

    @pytest.mark.asyncio
    async def test_error_result_sets_is_error(self, builder_mod):
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_result(tools, "lookup", "boom", is_error=True)

        assert json.loads(tools.send_event.call_args.kwargs["content"]) == {
            "name": "lookup",
            "output": "boom",
            "is_error": True,
        }

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_propagate(self, builder_mod):
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock(side_effect=Exception("403 Forbidden"))

        # Both must not raise
        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result", is_error=True)


class TestNoopReporter:
    @pytest.mark.asyncio
    async def test_never_calls_send_event(self, builder_mod):
        reporter = builder_mod.NoopReporter()
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()


# --- Missing-context error JSON ---


class TestMissingContext:
    def test_tool_returns_error_json_when_get_context_returns_none(self, builder_mod):
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message_tool = next(t for t in tools if t.name == "band_send_message")
        result_str = send_message_tool._run(content="hi", mentions=[])
        result = json.loads(result_str)
        assert result["status"] == "error"
        assert "No room context available" in result["message"]


# --- run_async + nest_asyncio lazy patch ---


class TestRunAsyncLazyPatch:
    def test_apply_lazy_only_once(self, runtime_mod, crewai_mocks):
        runtime_mod._nest_asyncio_applied = False
        crewai_mocks.reset_mock()

        async def coro_value() -> str:
            return "ok"

        runtime_mod.run_async(coro_value())
        runtime_mod.run_async(coro_value())
        runtime_mod.run_async(coro_value())

        # nest_asyncio.apply should have been called exactly once across
        # multiple run_async invocations (the lazy patch).
        assert crewai_mocks.apply.call_count == 1


class TestStoreMemoryArgsSchema:
    """CrewAI advertises the master model, so master text and validators apply."""

    def test_type_description_comes_from_master(self, platform_args_schemas) -> None:
        from band.core.memory_types import memory_type_field_description

        schema = platform_args_schemas["band_store_memory"]
        assert (
            schema.model_fields["type"].description == memory_type_field_description()
        )

    def test_rejects_subject_scope_without_subject_id(
        self, platform_args_schemas
    ) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="requires a subject_id"):
            platform_args_schemas["band_store_memory"].model_validate(
                {
                    "content": "remember this",
                    "system": "working",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "subject",
                }
            )

    def test_rejects_type_for_wrong_system(self, platform_args_schemas) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError, match="type `semantic` is not valid for system `sensory`"
        ):
            platform_args_schemas["band_store_memory"].model_validate(
                {
                    "content": "remember this",
                    "system": "sensory",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "organization",
                }
            )
