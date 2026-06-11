"""Tests for the shared CrewAI tool builder in band.integrations.crewai.

These tests cover the extracted surface (build_band_crewai_tools, the
reporter implementations, and run_async behavior) without going through
either CrewAIAdapter or CrewAIFlowAdapter — the builder is the seam they
both consume.
"""

from __future__ import annotations

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

    for mod in (
        "band.integrations.crewai",
        "band.integrations.crewai.runtime",
        "band.integrations.crewai.tools",
    ):
        sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    try:
        yield mock_nest_asyncio
    finally:
        for mod in (
            "band.integrations.crewai",
            "band.integrations.crewai.runtime",
            "band.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)


@pytest.fixture
def builder_mod(crewai_mocks):
    import importlib

    return importlib.import_module("band.integrations.crewai.tools")


@pytest.fixture
def runtime_mod(crewai_mocks):
    import importlib

    return importlib.import_module("band.integrations.crewai.runtime")


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

    def test_both_capabilities(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
        )
        assert len(tools) == 17  # 7 base + 5 contacts + 5 memory

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
            ("band_list_memories", {"memory_type": "fact"}),
            (
                "band_store_memory",
                {
                    "content": "remember this",
                    "system": "working",
                    "memory_type": "fact",
                    "segment": "user",
                    "thought": "useful later",
                },
            ),
        ],
    )
    def test_platform_tool_schemas_reject_invalid_values(
        self, builder_mod, tool_name, payload
    ):
        from pydantic import ValidationError

        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
        )
        tool = next(t for t in tools if t.name == tool_name)

        with pytest.raises(ValidationError):
            tool.args_schema.model_validate(payload)

    def test_platform_tool_schemas_accept_metadata_fields(self, builder_mod):
        from band.core.types import Capability

        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.MEMORY}),
        )
        send_event = next(t for t in tools if t.name == "band_send_event")
        store_memory = next(t for t in tools if t.name == "band_store_memory")

        assert send_event.args_schema.model_validate(
            {
                "content": "state update",
                "message_type": "task",
                "metadata": {"run_id": "run-1"},
            }
        ).metadata == {"run_id": "run-1"}
        assert store_memory.args_schema.model_validate(
            {
                "content": "remember this",
                "system": "working",
                "memory_type": "semantic",
                "segment": "user",
                "thought": "useful later",
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

    def test_send_message_marks_reply_tracker(self, builder_mod):
        """A successful band_send_message flips the per-turn ReplyTracker so
        the adapter can treat a later empty final answer as benign."""
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

        result = json.loads(send_message._run(content="hello", mentions="[]"))

        assert result["status"] == "success"
        tools_obj.send_message.assert_awaited_once()
        assert tracker.replied is True

    def test_reply_tracker_not_marked_on_send_failure(self, builder_mod):
        """A failed send must NOT mark the tracker — the turn produced no reply."""
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

        result = json.loads(send_message._run(content="hello", mentions="[]"))

        assert result["status"] == "error"
        assert tracker.replied is False


# --- Reporter behavior ---


class TestEmitExecutionReporter:
    @pytest.mark.asyncio
    async def test_does_not_emit_when_emit_execution_unset(self, builder_mod):
        from band.core.types import AdapterFeatures

        features = AdapterFeatures()  # empty emit set
        reporter = builder_mod.EmitExecutionReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_emit_execution_set(self, builder_mod):
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.EXECUTION}))
        reporter = builder_mod.EmitExecutionReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        assert tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_propagate(self, builder_mod):
        from band.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.EXECUTION}))
        reporter = builder_mod.EmitExecutionReporter(features)
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
        result_str = send_message_tool._run(content="hi", mentions="[]")
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


class TestStoreMemoryInputDescription:
    def test_crewai_store_memory_type_description_is_generated(self, builder_mod):
        """CrewAI store_memory args schema should use memory_type_field_description()."""
        from thenvoi.core.memory_types import memory_type_field_description

        expected = memory_type_field_description()
        assert (
            builder_mod._StoreMemoryInput.model_fields["memory_type"].description
            == expected
        )
        schema = builder_mod._StoreMemoryInput.model_json_schema()
        assert schema["properties"]["memory_type"]["description"] == expected

    def test_crewai_store_memory_rejects_subject_scope_without_subject_id(
        self, builder_mod
    ) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="requires a subject_id"):
            builder_mod._StoreMemoryInput.model_validate(
                {
                    "content": "remember this",
                    "system": "working",
                    "memory_type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "subject",
                }
            )
