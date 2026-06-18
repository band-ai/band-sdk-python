"""Agno adapter behavior tests.

Conformance already covers init defaults, ``on_started`` name/description, and
generic converter wiring; these tests pin Agno-only behavior: agent deep-copy,
memory-collision warning, Band-tool wiring, the ContextVar tool binding,
fallback-send, emit reporting, transcript persistence, and cleanup. Rehydration
of platform history lives in ``test_rehydration.py``.
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agno.models.message import Message
from agno.run.agent import RunOutput

from band.adapters.agno import (
    AgnoAdapter,
    _bind_room_tools,
    _make_band_entrypoint,
    _strip_numeric_constraints,
)
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from band.testing import FakeAgentTools

from tests.adapters.agno.helpers import (
    SchemaTools,
    openai_tool_schema,
    tool_execution,
)


def _msg(
    room_id: str,
    content: str,
    *,
    msg_id: str = "m1",
    sender_id: str = "user-1",
) -> PlatformMessage:
    """A minimal PlatformMessage for driving on_message in a given room."""
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _factory_agent_stub() -> MagicMock:
    """A fake runtime agent as returned by an ``agent_factory``.

    Unlike the deep-copy path, the factory's agent is used as-is, so it carries
    the falsy history/memory defaults and its own ``deep_copy`` to assert the
    adapter never copies it.
    """
    agent = MagicMock(name="factory_agent")
    agent.update_memory_on_run = False
    agent.enable_agentic_memory = False
    agent.add_history_to_context = False
    agent.db = None
    agent.additional_context = None
    agent.add_tool = MagicMock()
    agent.arun = AsyncMock(return_value=RunOutput())
    agent.deep_copy = MagicMock()
    return agent


class TestOnStarted:
    async def test_runs_against_a_deep_copy_not_the_source(self, make_agno_agent):
        source, copy = make_agno_agent()
        adapter = AgnoAdapter(source)

        await adapter.on_started("TestBot", "desc")

        source.deep_copy.assert_called_once()
        assert adapter.agent is copy
        assert adapter.agent is not source

    async def test_syncs_converter_identity(self, make_started_adapter):
        adapter, _ = await make_started_adapter()

        assert adapter.history_converter._agent_name == "TestBot"


class TestAgentFactory:
    """``agent_factory`` mints the runtime agent at startup without deep_copy()."""

    def test_factory_not_called_in_init(self):
        factory = MagicMock(name="agent_factory")

        AgnoAdapter(agent_factory=factory)

        factory.assert_not_called()

    async def test_factory_called_once_in_on_started(self):
        runtime_agent = _factory_agent_stub()
        factory = MagicMock(name="agent_factory", return_value=runtime_agent)
        adapter = AgnoAdapter(agent_factory=factory)

        await adapter.on_started("TestBot", "desc")

        factory.assert_called_once_with()

    async def test_factory_agent_used_directly_not_deep_copied(self):
        runtime_agent = _factory_agent_stub()
        adapter = AgnoAdapter(agent_factory=lambda: runtime_agent)

        await adapter.on_started("TestBot", "desc")

        assert adapter.agent is runtime_agent
        # The factory's agent is used as-is; the adapter must not deep_copy it.
        runtime_agent.deep_copy.assert_not_called()

    async def test_factory_built_adapter_runs_the_agent_and_replies(self, tools):
        # End-to-end through the factory path: the factory's agent must be the
        # one actually run on a message, and its output delivered to the room.
        runtime_agent = _factory_agent_stub()
        runtime_agent.arun = AsyncMock(
            return_value=RunOutput(content="hi from factory")
        )
        adapter = AgnoAdapter(agent_factory=lambda: runtime_agent)
        await adapter.on_started("TestBot", "desc")

        await adapter.on_message(
            _msg("room-1", "hello"),
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        runtime_agent.arun.assert_awaited_once()
        tools.assert_message_sent(content="hi from factory", mentions=["user-1"])

    def test_neither_agent_nor_factory_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            AgnoAdapter()

    def test_both_agent_and_factory_raises(self, make_agno_agent):
        source, _ = make_agno_agent()

        with pytest.raises(ValueError, match="not both"):
            AgnoAdapter(source, agent_factory=lambda: source)


class TestMemoryCollisionWarning:
    """Collision is detected against the runtime agent at startup, not __init__."""

    async def test_warns_on_update_memory_on_run_with_memory_capability(
        self, make_agno_agent
    ):
        source, _ = make_agno_agent(update_memory_on_run=True)
        adapter = AgnoAdapter(
            source, features=AdapterFeatures(capabilities={Capability.MEMORY})
        )

        with pytest.warns(UserWarning, match="update_memory_on_run"):
            await adapter.on_started("TestBot", "desc")

    async def test_warns_on_agentic_memory_with_memory_capability(
        self, make_agno_agent
    ):
        source, _ = make_agno_agent(enable_agentic_memory=True)
        adapter = AgnoAdapter(
            source, features=AdapterFeatures(capabilities={Capability.MEMORY})
        )

        with pytest.warns(UserWarning, match="enable_agentic_memory"):
            await adapter.on_started("TestBot", "desc")

    async def test_no_warning_without_memory_capability(self, make_agno_agent):
        source, _ = make_agno_agent(
            update_memory_on_run=True, enable_agentic_memory=True
        )
        adapter = AgnoAdapter(source)  # no MEMORY capability -> no collision

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            await adapter.on_started("TestBot", "desc")


class TestBandToolWiring:
    async def test_wires_each_schema_once(
        self, make_started_adapter, sample_platform_message
    ):
        tools = SchemaTools(
            [
                openai_tool_schema("band_send_message"),
                openai_tool_schema("band_lookup_peers"),
            ]
        )
        adapter, copy = await make_started_adapter()

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # Second turn must not re-wire (idempotent by name via _wired_tool_names).
        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert copy.add_tool.call_count == 2
        wired_names = [call.args[0].name for call in copy.add_tool.call_args_list]
        assert wired_names == ["band_send_message", "band_lookup_peers"]

    async def test_capability_flags_drive_schema_request(
        self, make_started_adapter, sample_platform_message
    ):
        tools = SchemaTools([])
        adapter, _ = await make_started_adapter(
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS}
            )
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.schema_calls == [
            {"include_memory": True, "include_contacts": True}
        ]

    async def test_no_capabilities_excludes_memory_and_contacts(
        self, make_started_adapter, sample_platform_message
    ):
        tools = SchemaTools([])
        adapter, _ = await make_started_adapter()

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]


class TestBandInstructionInjection:
    """Drive a real Agno agent so we assert on the system prompt Agno actually
    assembled and sent to the model, not the attribute the adapter set."""

    @pytest.mark.parametrize(
        ("capabilities", "present", "absent"),
        [
            (set(), [], ["## Memory Tools", "## Contact Management Tools"]),
            (
                {Capability.MEMORY},
                ["## Memory Tools"],
                ["## Contact Management Tools"],
            ),
            (
                {Capability.CONTACTS},
                ["## Contact Management Tools"],
                ["## Memory Tools"],
            ),
        ],
    )
    async def test_capability_sections_gated_in_model_prompt(
        self, run_real_agent, sample_platform_message, capabilities, present, absent
    ):
        model = await run_real_agent(
            sample_platform_message,
            features=AdapterFeatures(capabilities=capabilities),
        )
        prompt = model.captured_system_prompt

        assert "## Environment" in prompt  # base guidance always injected
        assert all(section in prompt for section in present)
        assert all(section not in prompt for section in absent)

    async def test_developer_instructions_survive_in_prompt(
        self, run_real_agent, sample_platform_message
    ):
        model = await run_real_agent(
            sample_platform_message,
            instructions="You are Dev, a niche specialist.",
            additional_context="Keep replies under 10 words.",
        )
        prompt = model.captured_system_prompt

        assert "You are Dev, a niche specialist." in prompt
        assert "Keep replies under 10 words." in prompt
        assert "## Environment" in prompt


class TestBandEntrypointBinding:
    async def test_routes_to_execute_tool_call_inside_context(self, tools):
        entry = _make_band_entrypoint("band_lookup_peers")

        with _bind_room_tools(tools):
            result = await entry(page=1)

        assert tools.tool_calls == [
            {"tool_name": "band_lookup_peers", "arguments": {"page": 1}}
        ]
        assert json.loads(result) == {"status": "ok"}

    async def test_passes_string_results_through_unchanged(self):
        class _StrTools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                return "raw-string"

        entry = _make_band_entrypoint("band_lookup_peers")
        with _bind_room_tools(_StrTools()):
            assert await entry() == "raw-string"

    async def test_errors_outside_any_bound_context(self, tools):
        entry = _make_band_entrypoint("band_lookup_peers")

        # Bind then exit; the ContextVar must reset so later calls have no tools.
        with _bind_room_tools(tools):
            pass
        result = await entry(page=1)

        assert "no active Band context" in result
        assert tools.tool_calls == []


class TestReply:
    async def test_sends_fallback_text_when_agent_did_not_post(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(RunOutput(content="hello"))

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_message_sent(content="hello", mentions=["user-456"])

    async def test_skips_fallback_when_agent_called_band_send_message(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(
            content="hello", tools=[tool_execution("band_send_message")]
        )
        adapter, _ = await make_started_adapter(response)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_no_messages_sent()

    async def test_no_send_for_empty_content(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(RunOutput(content="   "))

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_no_messages_sent()


class TestEmitExecution:
    async def test_emits_tool_call_and_result_events(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(
            tools=[tool_execution("band_lookup_peers", args={"page": "1"}, result="ok")]
        )
        adapter, _ = await make_started_adapter(
            response, features=AdapterFeatures(emit={Emit.EXECUTION})
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        types = [e["message_type"] for e in tools.events_sent]
        assert types == ["tool_call", "tool_result"]
        call_payload = json.loads(tools.events_sent[0]["content"])
        result_payload = json.loads(tools.events_sent[1]["content"])
        assert call_payload == {
            "name": "band_lookup_peers",
            "args": {"page": "1"},
            "tool_call_id": "tc_1",
        }
        assert result_payload["output"] == "ok"
        assert result_payload["is_error"] is False

    async def test_self_reporting_tools_are_not_re_emitted(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(tools=[tool_execution("band_send_message")])
        adapter, _ = await make_started_adapter(
            response, features=AdapterFeatures(emit={Emit.EXECUTION})
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []

    async def test_no_events_without_execution_emit(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(tools=[tool_execution("band_lookup_peers")])
        adapter, _ = await make_started_adapter(response)  # no emit configured

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []


class TestEmitThoughts:
    async def test_emits_reasoning_as_thought(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(reasoning_content="thinking hard")
        adapter, _ = await make_started_adapter(
            response, features=AdapterFeatures(emit={Emit.THOUGHTS})
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_event_sent(message_type="thought")
        assert tools.events_sent[0]["content"] == "thinking hard"

    async def test_no_thought_without_thoughts_emit(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(reasoning_content="thinking hard")
        adapter, _ = await make_started_adapter(response)  # no emit configured

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []

    async def test_no_thought_for_blank_reasoning(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(
            RunOutput(reasoning_content="  "),
            features=AdapterFeatures(emit={Emit.THOUGHTS}),
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []


class TestPersistAndAccumulate:
    def test_persist_keeps_only_conversation_roles(self, make_agno_agent):
        source, _ = make_agno_agent()
        adapter = AgnoAdapter(source)
        response = RunOutput(
            messages=[
                Message(role="system", content="instructions"),
                Message(role="user", content="hi"),
                Message(role="assistant", content="hello"),
                Message(role="developer", content="state"),
                Message(role="tool", content="result"),
            ]
        )

        adapter._persist_turn("room-1", response)

        kept = [m.role for m in adapter._message_history["room-1"]]
        assert kept == ["user", "assistant", "tool"]

    def test_bootstrap_seeds_then_followup_accumulates(
        self, make_agno_agent, sample_platform_message
    ):
        source, _ = make_agno_agent()
        adapter = AgnoAdapter(source)
        seed = [Message(role="user", content="earlier")]

        adapter._build_run_input(
            sample_platform_message,
            seed,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        adapter._build_run_input(
            sample_platform_message,
            [],
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        transcript = adapter._message_history["room-1"]
        # seed + bootstrap user msg + follow-up user msg
        assert len(transcript) == 3
        assert transcript[0].content == "earlier"
        assert all(m.role == "user" for m in transcript)


class TestOnCleanup:
    async def test_drops_room_transcript(self, make_agno_agent):
        source, _ = make_agno_agent()
        adapter = AgnoAdapter(source)
        adapter._message_history["room-1"] = [Message(role="user", content="hi")]

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._message_history

    async def test_unknown_room_is_noop(self, make_agno_agent):
        source, _ = make_agno_agent()
        adapter = AgnoAdapter(source)

        await adapter.on_cleanup("never-seen")  # must not raise


class TestUsedBeforeStarted:
    async def test_run_agent_before_on_started_raises(self, make_agno_agent):
        source, _ = make_agno_agent()
        adapter = AgnoAdapter(source)

        with pytest.raises(RuntimeError, match="before on_started"):
            await adapter._run_agent(
                [], FakeAgentTools(), room_id="room-1", msg_id="m1"
            )


class TestSessionIsolation:
    async def test_arun_uses_room_id_as_session_id(self, make_started_adapter):
        adapter, copy = await make_started_adapter()

        await adapter.on_message(
            _msg("room-A", "hi"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert copy.arun.await_args.kwargs["session_id"] == "room-A"

    async def test_custom_session_id_factory_is_used(self, make_agno_agent):
        source, copy = make_agno_agent()
        adapter = AgnoAdapter(source, session_id_factory=lambda room: f"sess::{room}")
        await adapter.on_started("TestBot", "desc")

        await adapter.on_message(
            _msg("room-A", "hi"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert copy.arun.await_args.kwargs["session_id"] == "sess::room-A"

    async def test_two_rooms_get_isolated_sessions_and_inputs(
        self, make_started_adapter
    ):
        adapter, copy = await make_started_adapter()

        await adapter.on_message(
            _msg("room-A", "alpha-secret"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        await adapter.on_message(
            _msg("room-B", "beta-secret"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-B",
        )

        calls = copy.arun.await_args_list
        assert calls[0].kwargs["session_id"] == "room-A"
        assert calls[1].kwargs["session_id"] == "room-B"

        room_b_input = " ".join(m.content or "" for m in calls[1].kwargs["input"])
        assert "beta-secret" in room_b_input
        assert "alpha-secret" not in room_b_input


class TestHubContactExposure:
    """The adapter decides contact exposure (mirrors LangGraph): the CONTACTS
    capability OR a hub room force-includes contact tool schemas."""

    async def test_normal_room_does_not_request_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], room_id="room-A")

        await adapter.on_message(
            _msg("room-A", "hi"),
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]

    async def test_hub_room_forces_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], hub_room_id="hub", room_id="hub")

        await adapter.on_message(
            _msg("hub", "hi"),
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="hub",
        )

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": True}
        ]

    async def test_contact_tools_added_additively_after_hub(self, make_started_adapter):
        adapter, copy = await make_started_adapter()

        normal = SchemaTools(
            [openai_tool_schema("band_send_message")], room_id="room-A"
        )
        await adapter.on_message(
            _msg("room-A", "hi"),
            normal,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        assert [c.args[0].name for c in copy.add_tool.call_args_list] == [
            "band_send_message"
        ]

        hub = SchemaTools(
            [
                openai_tool_schema("band_send_message"),
                openai_tool_schema("band_add_contact"),
            ],
            hub_room_id="hub",
            room_id="hub",
        )
        await adapter.on_message(
            _msg("hub", "hi"),
            hub,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="hub",
        )

        # band_send_message is not re-added; band_add_contact is additively wired.
        wired = [c.args[0].name for c in copy.add_tool.call_args_list]
        assert wired == ["band_send_message", "band_add_contact"]
        # A run still executes per message against the single shared agent.
        assert copy.arun.await_count == 2


class TestSchemaSanitization:
    """Band pagination params carry numeric-range keywords (Pydantic ge/le ->
    minimum/maximum); some providers reject those on integers, so the adapter
    strips them before wiring tools into Agno."""

    def test_strip_removes_range_keywords_recursively(self):
        schema = {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "name": {"type": "string"},
            },
        }

        cleaned = _strip_numeric_constraints(schema)

        assert cleaned["properties"]["page"] == {"type": "integer"}
        assert cleaned["properties"]["page_size"] == {"type": "integer"}
        assert cleaned["properties"]["name"] == {"type": "string"}
        # The input schema is left untouched (a new structure is returned).
        assert "maximum" in schema["properties"]["page_size"]

    async def test_wired_tool_schema_has_no_numeric_constraints(
        self, make_started_adapter
    ):
        schema = {
            "type": "function",
            "function": {
                "name": "band_lookup_peers",
                "description": "lookup peers",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                },
            },
        }
        adapter, copy = await make_started_adapter()

        await adapter.on_message(
            _msg("room-1", "hi"),
            SchemaTools([schema]),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        wired = copy.add_tool.call_args_list[0].args[0]
        page_size = wired.parameters["properties"]["page_size"]
        assert page_size == {"type": "integer"}


class TestFeatureFilters:
    """AdapterFeatures include/exclude/category filters gate which Band tools
    are wired (parity with LangGraph)."""

    ALL_SCHEMAS = [
        openai_tool_schema("band_send_message"),  # chat
        openai_tool_schema("band_lookup_peers"),  # chat
        openai_tool_schema("band_store_memory"),  # memory
        openai_tool_schema("band_add_contact"),  # contacts
    ]

    async def _wired_names(self, adapter, copy) -> list[str]:
        await adapter.on_message(
            _msg("room-A", "hi"),
            SchemaTools(self.ALL_SCHEMAS),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        return [c.args[0].name for c in copy.add_tool.call_args_list]

    async def test_include_tools_keeps_only_named(self, make_started_adapter):
        adapter, copy = await make_started_adapter(
            features=AdapterFeatures(include_tools=["band_send_message"])
        )

        assert await self._wired_names(adapter, copy) == ["band_send_message"]

    async def test_exclude_tools_drops_named(self, make_started_adapter):
        adapter, copy = await make_started_adapter(
            features=AdapterFeatures(exclude_tools=["band_send_message"])
        )

        names = await self._wired_names(adapter, copy)
        assert "band_send_message" not in names
        assert "band_lookup_peers" in names

    async def test_include_categories_keeps_only_category(self, make_started_adapter):
        adapter, copy = await make_started_adapter(
            features=AdapterFeatures(include_categories=["chat"])
        )

        assert sorted(await self._wired_names(adapter, copy)) == [
            "band_lookup_peers",
            "band_send_message",
        ]


class TestRunFailureReporting:
    async def test_emits_generic_error_event_and_reraises(
        self, make_started_adapter, tools
    ):
        adapter, copy = await make_started_adapter()
        copy.arun.side_effect = RuntimeError("db dsn leaked: secret-token")

        with pytest.raises(RuntimeError):
            await adapter.on_message(
                _msg("room-A", "hi"),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )

        errors = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(errors) == 1
        assert (
            errors[0]["content"]
            == "Internal error while processing message; see agent logs."
        )
        # The exception text (which can carry secrets) must not leak to the room.
        assert "secret-token" not in errors[0]["content"]

    async def test_error_event_failure_does_not_mask_original(
        self, make_started_adapter
    ):
        adapter, copy = await make_started_adapter()
        copy.arun.side_effect = RuntimeError("boom")

        class _FailingEventTools(FakeAgentTools):
            async def send_event(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                raise RuntimeError("event transport down")

        # The failed error-report must not replace the original exception.
        with pytest.raises(RuntimeError, match="boom"):
            await adapter.on_message(
                _msg("room-A", "hi"),
                _FailingEventTools(),
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )
