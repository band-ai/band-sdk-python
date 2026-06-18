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
from typing import Any

import pytest
from agno.models.message import Message
from agno.run.agent import RunOutput

from band.adapters.agno import (
    AgnoAdapter,
    _bind_room_tools,
    _make_band_entrypoint,
)
from band.core.types import AdapterFeatures, Capability, Emit
from band.testing import FakeAgentTools

from tests.adapters.agno.helpers import (
    SchemaTools,
    openai_tool_schema,
    tool_execution,
)


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


class TestMemoryCollisionWarning:
    def test_warns_on_update_memory_on_run_with_memory_capability(
        self, make_agno_agent
    ):
        source, _ = make_agno_agent(update_memory_on_run=True)

        with pytest.warns(UserWarning, match="update_memory_on_run"):
            AgnoAdapter(
                source, features=AdapterFeatures(capabilities={Capability.MEMORY})
            )

    def test_warns_on_agentic_memory_with_memory_capability(self, make_agno_agent):
        source, _ = make_agno_agent(enable_agentic_memory=True)

        with pytest.warns(UserWarning, match="enable_agentic_memory"):
            AgnoAdapter(
                source, features=AdapterFeatures(capabilities={Capability.MEMORY})
            )

    def test_no_warning_without_memory_capability(self, make_agno_agent):
        source, _ = make_agno_agent(
            update_memory_on_run=True, enable_agentic_memory=True
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            AgnoAdapter(source)  # no MEMORY capability -> no collision


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
        # Second turn must not re-wire (the _band_tools_wired guard).
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
