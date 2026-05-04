"""Tests for the shared CrewAI tool builder in thenvoi.integrations.crewai.

These tests cover the extracted surface (build_thenvoi_crewai_tools, the
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
        "thenvoi.integrations.crewai",
        "thenvoi.integrations.crewai.runtime",
        "thenvoi.integrations.crewai.tools",
    ):
        sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    try:
        yield mock_nest_asyncio
    finally:
        for mod in (
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)


@pytest.fixture
def builder_mod(crewai_mocks):
    import importlib

    return importlib.import_module("thenvoi.integrations.crewai.tools")


@pytest.fixture
def runtime_mod(crewai_mocks):
    import importlib

    return importlib.import_module("thenvoi.integrations.crewai.runtime")


# --- Tool-set composition ---


class TestToolSetComposition:
    def test_base_tools_only(self, builder_mod):

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        names = {t.name for t in tools}
        assert names == {
            "thenvoi_send_message",
            "thenvoi_send_event",
            "thenvoi_add_participant",
            "thenvoi_remove_participant",
            "thenvoi_get_participants",
            "thenvoi_lookup_peers",
            "thenvoi_create_chatroom",
        }
        assert len(tools) == 7

    def test_capability_contacts_adds_five(self, builder_mod):
        from thenvoi.core.types import Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS}),
        )
        names = {t.name for t in tools}
        contact_names = {
            "thenvoi_list_contacts",
            "thenvoi_add_contact",
            "thenvoi_remove_contact",
            "thenvoi_list_contact_requests",
            "thenvoi_respond_contact_request",
        }
        assert contact_names.issubset(names)
        assert len(tools) == 12

    def test_capability_memory_adds_five(self, builder_mod):
        from thenvoi.core.types import Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.MEMORY}),
        )
        names = {t.name for t in tools}
        memory_names = {
            "thenvoi_list_memories",
            "thenvoi_store_memory",
            "thenvoi_get_memory",
            "thenvoi_supersede_memory",
            "thenvoi_archive_memory",
        }
        assert memory_names.issubset(names)
        assert len(tools) == 12

    def test_both_capabilities(self, builder_mod):
        from thenvoi.core.types import Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
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

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
            custom_tools=[(MyInput, my_handler)],
        )
        # Custom tool name comes from the InputModel class name (lowercased)
        assert len(tools) == 8

    def test_adapter_feature_filters_apply_to_platform_tools(self, builder_mod):
        from thenvoi.core.types import AdapterFeatures, Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
                include_categories=("contacts", "memory"),
                exclude_tools=("thenvoi_remove_contact", "thenvoi_archive_memory"),
            ),
        )

        names = {t.name for t in tools}
        assert "thenvoi_send_message" not in names
        assert "thenvoi_list_contacts" in names
        assert "thenvoi_list_memories" in names
        assert "thenvoi_remove_contact" not in names
        assert "thenvoi_archive_memory" not in names

    def test_adapter_feature_filters_only_apply_to_platform_tools(self, builder_mod):
        from pydantic import BaseModel

        from thenvoi.core.types import AdapterFeatures

        class MyInput(BaseModel):
            value: str

        class OtherInput(BaseModel):
            value: str

        async def handler(_: BaseModel) -> str:
            return "ok"

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            features=AdapterFeatures(
                include_tools=("thenvoi_send_message", "myinput"),
                exclude_tools=("myinput",),
            ),
            custom_tools=[(MyInput, handler), (OtherInput, handler)],
        )

        names = {t.name for t in tools}
        assert names == {"thenvoi_send_message", "my", "other"}

    @pytest.mark.parametrize(
        ("tool_name", "payload"),
        [
            ("thenvoi_send_event", {"content": "thinking", "message_type": "debug"}),
            ("thenvoi_add_participant", {"identifier": "peer", "role": "viewer"}),
            ("thenvoi_lookup_peers", {"page_size": 101}),
            ("thenvoi_list_contacts", {"page": 0}),
            ("thenvoi_list_contact_requests", {"sent_status": "done"}),
            ("thenvoi_respond_contact_request", {"action": "maybe"}),
            ("thenvoi_list_memories", {"memory_type": "fact"}),
            (
                "thenvoi_store_memory",
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

        from thenvoi.core.types import Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY}),
        )
        tool = next(t for t in tools if t.name == tool_name)

        with pytest.raises(ValidationError):
            tool.args_schema.model_validate(payload)

    def test_platform_tool_schemas_accept_metadata_fields(self, builder_mod):
        from thenvoi.core.types import Capability

        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset({Capability.MEMORY}),
        )
        send_event = next(t for t in tools if t.name == "thenvoi_send_event")
        store_memory = next(t for t in tools if t.name == "thenvoi_store_memory")

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
        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        lookup_peers = next(t for t in tools if t.name == "thenvoi_lookup_peers")

        result = json.loads(lookup_peers._run(page=2, page_size=25))

        assert result["status"] == "success"
        tools_obj.lookup_peers.assert_awaited_once_with(2, 25)


# --- Reporter behavior ---


class TestEmitExecutionReporter:
    @pytest.mark.asyncio
    async def test_does_not_emit_when_emit_execution_unset(self, builder_mod):
        from thenvoi.core.types import AdapterFeatures

        features = AdapterFeatures()  # empty emit set
        reporter = builder_mod.EmitExecutionReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_emit_execution_set(self, builder_mod):
        from thenvoi.core.types import AdapterFeatures, Emit

        features = AdapterFeatures(emit=frozenset({Emit.EXECUTION}))
        reporter = builder_mod.EmitExecutionReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        assert tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_propagate(self, builder_mod):
        from thenvoi.core.types import AdapterFeatures, Emit

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
        tools = builder_mod.build_thenvoi_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message_tool = next(t for t in tools if t.name == "thenvoi_send_message")
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
