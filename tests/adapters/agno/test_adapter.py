"""Agno adapter behavior tests.

Conformance already covers init defaults, ``on_started`` name/description, and
generic converter wiring; these tests pin Agno-only behavior: running against
the given agent, memory-collision warning, per-run Band-tool resolution (the
callable-tools factory + ContextVar binding), strict per-room tool visibility,
fallback-send, emit reporting, transcript persistence, and cleanup. Rehydration
of platform history lives in ``test_rehydration.py``.
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from agno.agent import Agent as AgnoAgent
from agno.models.message import Message
from agno.run.agent import RunOutput

from band.adapters.agno import (
    AgnoAdapter,
    _bind_room_tools,
    _make_band_entrypoint,
)
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from band.testing import FakeAgentTools

from tests.adapters.agno.helpers import (
    CapturingModel,
    ContactAwareTools,
    SchemaTools,
    openai_tool_schema,
    run_input,
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


class TestOnStarted:
    async def test_runs_against_the_given_agent(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)

        await adapter.on_started("TestBot", "desc")

        # The adapter uses the caller's instance directly, no copy.
        assert adapter.agent is agent

    async def test_syncs_converter_identity(self, make_started_adapter):
        adapter, _ = await make_started_adapter()

        assert adapter.history_converter._agent_name == "TestBot"

    async def test_runs_the_given_agent_on_a_message(self, make_agno_agent, tools):
        # End-to-end: the given agent must be the one actually run on a message.
        # The adapter delivers nothing on its own; plain agent text is not sent
        # (only a ``band_send_message`` tool call reaches the room).
        agent = make_agno_agent(response=RunOutput(content="hi there"))
        adapter = AgnoAdapter(agent)
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

        agent.arun.assert_awaited_once()
        tools.assert_no_messages_sent()


class TestMemoryCollisionWarning:
    """Collision is detected against the runtime agent at startup, not __init__."""

    async def test_warns_on_update_memory_on_run_with_memory_capability(
        self, make_agno_agent
    ):
        agent = make_agno_agent(update_memory_on_run=True)
        adapter = AgnoAdapter(
            agent, features=AdapterFeatures(capabilities={Capability.MEMORY})
        )

        with pytest.warns(UserWarning, match="update_memory_on_run"):
            await adapter.on_started("TestBot", "desc")

    async def test_warns_on_agentic_memory_with_memory_capability(
        self, make_agno_agent
    ):
        agent = make_agno_agent(enable_agentic_memory=True)
        adapter = AgnoAdapter(
            agent, features=AdapterFeatures(capabilities={Capability.MEMORY})
        )

        with pytest.warns(UserWarning, match="enable_agentic_memory"):
            await adapter.on_started("TestBot", "desc")

    async def test_no_warning_without_memory_capability(self, make_agno_agent):
        agent = make_agno_agent(update_memory_on_run=True, enable_agentic_memory=True)
        adapter = AgnoAdapter(agent)  # no MEMORY capability -> no collision

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            await adapter.on_started("TestBot", "desc")


class TestRoomToolResolution:
    """Band tools are exposed per-run via the ``_resolve_room_tools`` factory
    Agno calls each turn, not wired onto the agent. These pin what that factory
    returns and the schema requests it makes for the active room."""

    async def test_resolves_band_tools_for_active_room(self, make_started_adapter):
        tools = SchemaTools(
            [
                openai_tool_schema("band_send_message"),
                openai_tool_schema("band_lookup_peers"),
            ]
        )
        adapter, _ = await make_started_adapter()

        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()

        assert [fn.name for fn in resolved] == [
            "band_send_message",
            "band_lookup_peers",
        ]

    async def test_no_band_tools_outside_a_bound_room(self, make_started_adapter):
        # Defensive: with no active room bound, the factory exposes no Band tools
        # (and does not even request schemas) rather than guessing visibility.
        tools = SchemaTools([openai_tool_schema("band_send_message")])
        adapter, _ = await make_started_adapter()

        resolved = await adapter._resolve_room_tools()  # no _bind_room_tools

        assert resolved == []
        assert tools.schema_calls == []

    async def test_capability_flags_drive_schema_request(self, make_started_adapter):
        tools = SchemaTools([])
        adapter, _ = await make_started_adapter(
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS}
            )
        )

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": True, "include_contacts": True}
        ]

    async def test_schema_build_is_cached_across_runs(self, make_started_adapter):
        # Same contact flag across runs -> schemas are built once and reused,
        # not rebuilt every turn.
        tools = SchemaTools([openai_tool_schema("band_send_message")])
        adapter, _ = await make_started_adapter()

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()
            await adapter._resolve_room_tools()
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]

    async def test_user_tools_are_reincluded(self, make_agno_agent):
        # Replacing agent.tools with our factory must not drop the user's own
        # tools; they are re-included alongside the room's Band tools.
        user_tool = object()
        agent = make_agno_agent()
        agent.tools = [user_tool]
        adapter = AgnoAdapter(agent)
        await adapter.on_started("TestBot", "desc")

        tools = SchemaTools([openai_tool_schema("band_send_message")])
        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()

        assert resolved[0] is user_tool
        assert [getattr(t, "name", None) for t in resolved[1:]] == ["band_send_message"]


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

    async def test_guidance_injected_at_startup_before_any_message(
        self, make_started_adapter
    ):
        # Band guidance is injected in on_started, not lazily on first message.
        adapter, agent = await make_started_adapter()

        assert isinstance(agent.additional_context, str)
        assert "## Environment" in agent.additional_context


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
    """The adapter delivers nothing on its own. Like the other adapters, the
    agent must call ``band_send_message`` to reach the room; plain agent text is
    never auto-sent and the adapter never guesses a recipient."""

    async def test_no_send_when_agent_returns_only_text(
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

        tools.assert_no_messages_sent()

    async def test_no_send_when_agent_called_band_send_message(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # The tool call itself reaches the room; the adapter adds nothing on top.
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
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)
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

    def test_bootstrap_seeds_committed_transcript_from_history(
        self, make_agno_agent, sample_platform_message
    ):
        # Bootstrap seeds the committed transcript from rehydrated history. The
        # returned run input is that seed plus this turn's live message, but
        # building it must NOT push the live message into the committed store.
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)
        seed = [Message(role="user", content="earlier")]

        run_input_msgs = adapter._build_run_input(
            sample_platform_message,
            seed,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert [m.content for m in run_input_msgs] == [
            "earlier",
            sample_platform_message.format_for_llm(),
        ]
        # Committed transcript holds only the rehydrated seed.
        assert [m.content for m in adapter._message_history["room-1"]] == ["earlier"]

    def test_build_run_input_does_not_mutate_committed_transcript(
        self, make_agno_agent, sample_platform_message
    ):
        # A non-bootstrap turn reads the committed transcript but never writes to
        # it; the store is only ever advanced by _persist_turn after a run.
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)
        adapter._message_history["room-1"] = [Message(role="user", content="committed")]

        adapter._build_run_input(
            sample_platform_message,
            [],
            "participants",
            "contacts",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert [m.content for m in adapter._message_history["room-1"]] == ["committed"]


class TestFailedRunDoesNotContaminateNextTurn:
    async def test_failed_turn_leaves_no_residue_in_next_run_input(
        self, make_agno_agent, tools
    ):
        # Turn 1 raises mid-run; turn 2 succeeds. The injected system/user
        # messages from the failed turn must not survive into turn 2's input.
        agent = make_agno_agent()
        agent.arun = AsyncMock(
            side_effect=[RuntimeError("boom"), RunOutput(content="ok")]
        )
        adapter = AgnoAdapter(agent)
        await adapter.on_started("TestBot", "desc")

        first = _msg("room-1", "first question", msg_id="m1")
        with pytest.raises(RuntimeError):
            await adapter.on_message(
                first,
                tools,
                [],
                "P1-participants",
                "C1-contacts",
                is_session_bootstrap=True,
                room_id="room-1",
            )

        second = _msg("room-1", "second question", msg_id="m2")
        await adapter.on_message(
            second,
            tools,
            [],
            "P2-participants",
            "C2-contacts",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        contents = [m.content for m in run_input(agent)]
        # No residue from the failed turn 1.
        assert not any("P1-participants" in c for c in contents)
        assert not any("C1-contacts" in c for c in contents)
        assert first.format_for_llm() not in contents
        # Turn 2's own injected context and live message are present.
        assert any("P2-participants" in c for c in contents)
        assert contents[-1] == second.format_for_llm()


class TestOnCleanup:
    async def test_drops_room_transcript(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)
        adapter._message_history["room-1"] = [Message(role="user", content="hi")]

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._message_history

    async def test_unknown_room_is_noop(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)

        await adapter.on_cleanup("never-seen")  # must not raise


class TestUsedBeforeStarted:
    async def test_run_agent_before_on_started_raises(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent)

        with pytest.raises(RuntimeError, match="before on_started"):
            await adapter._run_agent(
                [], FakeAgentTools(), room_id="room-1", msg_id="m1"
            )


class TestSessionIsolation:
    async def test_arun_uses_room_id_as_session_id(self, make_started_adapter):
        adapter, agent = await make_started_adapter()

        await adapter.on_message(
            _msg("room-A", "hi"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert agent.arun.await_args.kwargs["session_id"] == "room-A"

    async def test_custom_session_id_factory_is_used(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent, session_id_factory=lambda room: f"sess::{room}")
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

        assert agent.arun.await_args.kwargs["session_id"] == "sess::room-A"

    async def test_two_rooms_get_isolated_sessions_and_inputs(
        self, make_started_adapter
    ):
        adapter, agent = await make_started_adapter()

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

        calls = agent.arun.await_args_list
        assert calls[0].kwargs["session_id"] == "room-A"
        assert calls[1].kwargs["session_id"] == "room-B"

        room_b_input = " ".join(m.content or "" for m in calls[1].kwargs["input"])
        assert "beta-secret" in room_b_input
        assert "alpha-secret" not in room_b_input


class TestHubContactExposure:
    """The adapter decides contact exposure (mirrors LangGraph): the CONTACTS
    capability OR a hub room force-includes contact tool schemas, resolved per
    run so visibility is strictly per-room."""

    async def test_normal_room_does_not_request_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], room_id="room-A")

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]

    async def test_hub_room_forces_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], hub_room_id="hub", room_id="hub")

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": True}
        ]

    async def test_contacts_do_not_leak_into_normal_room_after_hub(
        self, make_started_adapter
    ):
        # Core regression: after a hub room exposes contact tools, a subsequent
        # normal room's resolution must NOT include them. The old additive wiring
        # accumulated the union on the shared agent; per-run resolution does not.
        adapter, _ = await make_started_adapter()

        hub = ContactAwareTools(hub_room_id="hub", room_id="hub")
        with _bind_room_tools(hub):
            hub_names = [fn.name for fn in await adapter._resolve_room_tools()]
        assert "band_add_contact" in hub_names

        normal = ContactAwareTools(room_id="room-A")
        with _bind_room_tools(normal):
            normal_names = [fn.name for fn in await adapter._resolve_room_tools()]

        assert normal_names == ["band_send_message"]
        assert "band_add_contact" not in normal_names


class TestFeatureFilters:
    """AdapterFeatures include/exclude/category filters gate which Band tools
    are wired (parity with LangGraph)."""

    ALL_SCHEMAS = [
        openai_tool_schema("band_send_message"),  # chat
        openai_tool_schema("band_lookup_peers"),  # chat
        openai_tool_schema("band_store_memory"),  # memory
        openai_tool_schema("band_add_contact"),  # contacts
    ]

    async def _resolved_names(self, adapter) -> list[str]:
        tools = SchemaTools(self.ALL_SCHEMAS)
        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()
        return [fn.name for fn in resolved]

    async def test_include_tools_keeps_only_named(self, make_started_adapter):
        adapter, _ = await make_started_adapter(
            features=AdapterFeatures(include_tools=["band_send_message"])
        )

        assert await self._resolved_names(adapter) == ["band_send_message"]

    async def test_exclude_tools_drops_named(self, make_started_adapter):
        adapter, _ = await make_started_adapter(
            features=AdapterFeatures(exclude_tools=["band_send_message"])
        )

        names = await self._resolved_names(adapter)
        assert "band_send_message" not in names
        assert "band_lookup_peers" in names

    async def test_include_categories_keeps_only_category(self, make_started_adapter):
        adapter, _ = await make_started_adapter(
            features=AdapterFeatures(include_categories=["chat"])
        )

        assert sorted(await self._resolved_names(adapter)) == [
            "band_lookup_peers",
            "band_send_message",
        ]


class TestRunFailureReporting:
    async def test_emits_generic_error_event_and_reraises(
        self, make_started_adapter, tools
    ):
        adapter, agent = await make_started_adapter()
        agent.arun.side_effect = RuntimeError("db dsn leaked: secret-token")

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
        adapter, agent = await make_started_adapter()
        agent.arun.side_effect = RuntimeError("boom")

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


class TestPerRunToolExposureEndToEnd:
    """Drive a real Agno agent so we assert on the tools Agno actually offered
    the model per run -- proving the factory is installed and invoked per turn,
    and that contact tools do not leak across rooms through the shared agent."""

    async def test_model_receives_only_active_room_tools(self):
        model = CapturingModel()
        agno = AgnoAgent(model=model, instructions="You are Dev.")
        adapter = AgnoAdapter(agno)
        await adapter.on_started("Bot", "desc")

        # Hub room: contact tools are offered to the model.
        hub = ContactAwareTools(hub_room_id="hub", room_id="hub")
        await adapter.on_message(
            _msg("hub", "hi"),
            hub,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="hub",
        )
        assert model.captured_tool_names is not None
        assert "band_add_contact" in model.captured_tool_names

        # Normal room afterwards on the same shared agent: no contact leak.
        normal = ContactAwareTools(room_id="room-A")
        await adapter.on_message(
            _msg("room-A", "hi"),
            normal,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        assert model.captured_tool_names == ["band_send_message"]
