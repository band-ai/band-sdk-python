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
from collections.abc import Iterable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from band.core.exceptions import BandToolError
from band.core.memory_types import memory_type_field_description
from band.core.types import AdapterFeatures, Capability, Emit
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import (
    BandTool,
    TurnEffect,
    file_content_placeholder,
    image_block_placeholder,
    iter_tool_definitions,
)
from band.testing.fake_tools import FakeAgentTools


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

    return importlib.import_module("band.integrations.crewai.tools")


@pytest.fixture
def runtime_mod(crewai_mocks):

    return importlib.import_module("band.integrations.crewai.runtime")


@pytest.fixture
def platform_args_schemas(builder_mod):
    """Tool name -> the args schema CrewAI actually advertises to the LLM."""

    tools = builder_mod.build_band_crewai_tools(
        get_context=lambda: None,
        reporter=builder_mod.NoopReporter(),
        capabilities=frozenset(
            {Capability.CONTACTS, Capability.MEMORY, Capability.FILES}
        ),
    )
    return {tool.name: tool.args_schema for tool in tools}


# --- Tool-set composition ---


def _registry_names(capabilities: frozenset[Capability]) -> set[str]:
    """The platform tools the registry offers for ``capabilities``."""
    return {
        definition.name
        for definition in iter_tool_definitions(capabilities=capabilities)
    }


def _unique_names(tools: Iterable[Any]) -> set[str]:
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    return set(names)


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

        result = json.loads(lookup_peers._run())

        assert result["status"] == "success"
        assert result["peers"] == [{"id": "p1"}]
        result_event = tools_obj.send_event.call_args_list[-1]
        assert result_event.kwargs["message_type"] == "tool_result"
        reported = json.loads(result_event.kwargs["content"])
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

        features = AdapterFeatures()  # empty emit set
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_tool_calls_set(self, builder_mod):

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


# --- Turn ledger and tracker: the catalog calls the recording tool methods ---


class RecordInput(BaseModel):
    """Record a value."""

    value: str


async def record_value(args: RecordInput) -> str:
    return args.value


@declares_turn_effect(TurnEffect.ACT)
async def record_value_for_real(args: RecordInput) -> str:
    return args.value


@pytest.fixture
def room_tools() -> FakeAgentTools:
    return FakeAgentTools()


@pytest.fixture
def tracker(builder_mod):
    return builder_mod.ReplyTracker()


@pytest.fixture
def crew_tools(builder_mod, room_tools, tracker):
    """The crew's tools by name, bound to ``room_tools`` as the room context."""

    def build(custom_tools: list[Any] | None = None) -> dict[str, Any]:
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=room_tools, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            custom_tools=custom_tools,
        )
        return {tool.name: tool for tool in tools}

    return build


class TestTurnLedger:
    @pytest.mark.parametrize(
        ("tool", "arguments", "replied", "complete"),
        [
            (
                BandTool.SEND_MESSAGE,
                {"content": "hi", "mentions": ["@alice"]},
                True,
                True,
            ),
            (BandTool.NO_REPLY, {"reason": "FYI only"}, True, True),
            (BandTool.CREATE_CHATROOM, {}, False, True),
            (BandTool.LOOKUP_PEERS, {}, False, False),
            (
                BandTool.SEND_EVENT,
                {"content": "hm", "message_type": "thought"},
                False,
                False,
            ),
        ],
        ids=["reply", "decline", "act", "observe", "narrate"],
    )
    def test_a_tool_call_records_its_effect_on_the_turn(
        self, crew_tools, room_tools, tool, arguments, replied, complete
    ):
        crew_tools()[tool]._run(**arguments)

        assert (room_tools.turn.replied, room_tools.turn.complete) == (
            replied,
            complete,
        )

    def test_failed_send_then_event_leaves_the_turn_incomplete(
        self, crew_tools, room_tools
    ):
        """A rejected reply followed by narration is still a turn owed a reply."""
        room_tools.send_message_error = BandToolError("Unknown participant 'user1'")
        tools = crew_tools()

        tools[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@user1"])
        tools[BandTool.SEND_EVENT]._run(content="thinking", message_type="thought")

        assert (room_tools.messages_sent, len(room_tools.events_sent)) == ([], 1)
        assert not room_tools.turn.complete

    def test_tracker_keeps_what_a_successful_send_said(self, crew_tools, tracker):
        crew_tools()[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@alice"])

        assert (tracker.posts, tracker.any_tool_ran) == (["(to @alice) hi"], True)

    def test_a_failed_send_still_counts_as_a_tool_that_ran(
        self, crew_tools, room_tools, tracker
    ):
        room_tools.send_message_error = BandToolError("boom")

        crew_tools()[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@alice"])

        assert (tracker.posts, tracker.any_tool_ran) == ([], True)

    @pytest.mark.parametrize(
        ("handler", "completes"),
        [(record_value, False), (record_value_for_real, True)],
        ids=["undeclared-observes", "declared-act"],
    )
    def test_custom_tool_records_its_declared_effect(
        self, crew_tools, room_tools, handler, completes
    ):
        tool_name = get_custom_tool_name(RecordInput)

        crew_tools([(RecordInput, handler)])[tool_name]._run(value="x")

        assert room_tools.turn.complete is completes


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

        schema = platform_args_schemas["band_store_memory"]
        assert (
            schema.model_fields["type"].description == memory_type_field_description()
        )

    def test_rejects_subject_scope_without_subject_id(
        self, platform_args_schemas
    ) -> None:

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
