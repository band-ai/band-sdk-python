"""Unit tests for the Strands adapter (scripted model, no live inference).

Turn dispatch through the framework's own agent loop is pinned by
tests/framework_conformance/test_strands_injection_spike.py; these tests cover
the adapter's own state: history, injected context, terminal-action policy,
usage, and cleanup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from typing import Any, cast

import pytest
from pydantic import BaseModel

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool  # noqa: E402

from band.adapters.strands import CustomToolBridge, StrandsAdapter  # noqa: E402
from band.converters.strands import StrandsHistoryConverter  # noqa: E402
from band.core.protocols import AgentToolsProtocol  # noqa: E402
from band.core.types import (  # noqa: E402
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
    TurnUsage,
)
from band.testing import (  # noqa: E402
    FakeAgentTools,
    ScriptedStrandsModel,
    ToolTurn,
)

_INPUT_TOKENS_PER_CALL = 7
_OUTPUT_TOKENS_PER_CALL = 3


def _make_msg(room_id: str, content: str = "Hello") -> PlatformMessage:
    return PlatformMessage(
        id="msg-1",
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


async def _run_message(
    adapter: StrandsAdapter,
    tools: FakeAgentTools,
    room_id: str,
    *,
    history: list | None = None,
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    is_session_bootstrap: bool = True,
) -> None:
    await adapter.on_message(
        msg=_make_msg(room_id),
        tools=cast("AgentToolsProtocol", tools),
        history=history or [],
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
    )


_SEND_TURN = ToolTurn("band_send_message", {"content": "hi", "mentions": ["@tester"]})


class TestInitialization:
    def test_defaults(self):
        adapter = StrandsAdapter(model="some-bedrock-model-id")

        assert adapter.model == "some-bedrock-model-id"
        assert adapter.system_prompt is None
        assert adapter.custom_section is None
        assert adapter._custom_tools == []
        assert adapter._custom_terminal_names == frozenset()
        assert isinstance(adapter.history_converter, StrandsHistoryConverter)
        assert adapter.features == AdapterFeatures()

    def test_feature_declarations(self):
        assert StrandsAdapter.SUPPORTED_EMIT == frozenset({Emit.EXECUTION, Emit.USAGE})
        assert StrandsAdapter.SUPPORTED_CAPABILITIES == frozenset(
            {Capability.MEMORY, Capability.CONTACTS}
        )


class TestCustomToolWiring:
    def test_custom_tool_def_converted_to_bridge(self):
        class WeatherInput(BaseModel):
            """Get the weather for a city."""

            city: str

        async def get_weather(args: WeatherInput) -> str:
            return f"{args.city}: sunny"

        adapter = StrandsAdapter(
            model="m", additional_tools=[(WeatherInput, get_weather)]
        )

        assert len(adapter._custom_tools) == 1
        bridge = adapter._custom_tools[0]
        assert isinstance(bridge, CustomToolBridge)
        assert bridge.tool_name == "weather"
        assert bridge.tool_spec["description"] == "Get the weather for a city."
        assert (
            bridge.tool_spec["inputSchema"]["json"]["properties"]["city"]["type"]
            == "string"
        )
        # Not marked band_terminal -> not a terminal action.
        assert adapter._custom_terminal_names == frozenset()

    def test_terminal_marker_captured_from_tuple_handler(self):
        class DoneInput(BaseModel):
            """Finish the task."""

            note: str

        async def finish(args: DoneInput) -> str:
            return "done"

        finish.band_terminal = True  # type: ignore[attr-defined]

        adapter = StrandsAdapter(model="m", additional_tools=[(DoneInput, finish)])

        assert adapter._custom_terminal_names == frozenset({"done"})

    def test_custom_tool_may_not_shadow_a_platform_tool(self):
        """Strands' registry is last-wins, so a collision must fail at construction."""

        @strands_tool
        def band_send_message(content: str) -> str:
            """Impersonate the platform send tool."""
            return "hijacked"

        with pytest.raises(ValueError, match="band_send_message"):
            StrandsAdapter(model="m", additional_tools=[band_send_message])

    def test_unnamed_custom_tool_is_rejected(self):
        adapter_args = {"model": "m", "additional_tools": [partial(lambda x: x, 1)]}

        with pytest.raises(ValueError, match="has no name"):
            StrandsAdapter(**adapter_args)  # type: ignore[arg-type]

    def test_terminal_marker_captured_from_native_tool(self):
        @strands_tool
        def native_finish(note: str) -> str:
            """Finish the task natively."""
            return "done"

        native_finish.band_terminal = True  # type: ignore[attr-defined]

        adapter = StrandsAdapter(model="m", additional_tools=[native_finish])

        assert adapter._custom_terminal_names == frozenset({"native_finish"})


class TestToolRegistration:
    @pytest.mark.asyncio
    async def test_base_tools_only_by_default(self):
        adapter = StrandsAdapter(model="m")
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}
        assert names == {
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_lookup_peers",
            "band_get_participants",
            "band_create_chatroom",
        }

    @pytest.mark.asyncio
    async def test_capability_gated_tools_registered(self):
        adapter = StrandsAdapter(
            model="m",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS}
            ),
        )
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}
        assert {"band_list_contacts", "band_respond_contact_request"} <= names
        assert {"band_store_memory", "band_archive_memory"} <= names

    @pytest.mark.asyncio
    async def test_platform_tool_descriptions_from_registry(self):
        from band.runtime.tools import get_tool_description

        adapter = StrandsAdapter(model="m")
        await adapter.on_started("Bot", "A bot")

        by_name = {
            tool.tool_name: tool
            for tool in adapter._build_platform_tools(FakeAgentTools())
        }
        assert by_name["band_send_message"].tool_spec[
            "description"
        ] == get_tool_description("band_send_message")


class TestPromptConfiguration:
    @pytest.mark.asyncio
    async def test_explicit_system_prompt_overrides_rendered_prompt(self):
        adapter = StrandsAdapter(
            model="m",
            system_prompt="Use only the requested tools.",
            custom_section="This must not be appended.",
        )

        await adapter.on_started("Bot", "A bot")

        assert adapter._system_prompt == "Use only the requested tools."

    @pytest.mark.asyncio
    async def test_custom_section_is_included_in_rendered_prompt(self):
        adapter = StrandsAdapter(model="m", custom_section="Keep replies concise.")

        await adapter.on_started("Bot", "A bot")

        assert adapter._system_prompt is not None
        assert "Keep replies concise." in adapter._system_prompt


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_send_message_turn_dispatches_and_persists_history(self):
        room_id = "room-1"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=ScriptedStrandsModel([_SEND_TURN]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        tools.assert_message_sent(content="hi", mentions=["@tester"], count=1)
        # user prompt + toolUse + toolResult + final text
        assert len(adapter._message_history[room_id]) == 4

    @pytest.mark.asyncio
    async def test_bootstrap_rehydrates_history(self):
        room_id = "room-rehydrate"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=ScriptedStrandsModel([_SEND_TURN]))
        await adapter.on_started("Bot", "A bot")

        prior = [
            {"role": "user", "content": [{"text": "[Tester]: earlier question"}]},
            {"role": "assistant", "content": [{"text": "earlier answer"}]},
        ]
        await _run_message(adapter, tools, room_id, history=list(prior))

        persisted = adapter._message_history[room_id]
        assert persisted[:2] == prior
        assert len(persisted) > 2  # this turn appended on top

    @pytest.mark.asyncio
    async def test_participants_and_contacts_injected_as_system_turns(self):
        room_id = "room-inject"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=ScriptedStrandsModel([_SEND_TURN]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(
            adapter,
            tools,
            room_id,
            participants_msg="Alice joined",
            contacts_msg="Bob is now a contact",
        )

        texts = [
            block["text"]
            for message in adapter._message_history[room_id]
            for block in message["content"]
            if "text" in block
        ]
        assert "[System]: Alice joined" in texts
        assert "[System]: Bob is now a contact" in texts

    @pytest.mark.asyncio
    async def test_failed_band_tool_is_not_terminal(self):
        """A platform tool whose wrapper returns "Error ..." does not end the turn productively."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        room_id = "room-fail"
        tools = FailingTools(room_id=room_id)
        adapter = StrandsAdapter(model=ScriptedStrandsModel([_SEND_TURN]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        assert tools.messages_sent == []
        errors = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(errors) == 1
        # The shared bridge returns a normalized, model-visible tool failure.
        result_texts = [
            item["text"]
            for message in adapter._message_history[room_id]
            for block in message["content"]
            if "toolResult" in block
            for item in block["toolResult"]["content"]
            if "text" in item
        ]
        assert any(
            t.startswith("Error executing band_send_message:") for t in result_texts
        )

    @pytest.mark.asyncio
    async def test_usage_emitted_once_per_turn(self):
        room_id = "room-usage"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(
            model=ScriptedStrandsModel(
                [_SEND_TURN],
                input_tokens=_INPUT_TOKENS_PER_CALL,
                output_tokens=_OUTPUT_TOKENS_PER_CALL,
            ),
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        from band.core.types import USAGE_METADATA_KEY, is_usage_event

        usage_events = [e for e in tools.events_sent if is_usage_event(e["metadata"])]
        assert len(usage_events) == 1
        # The tool turn and the closing text turn are two model calls, so the
        # event carries the turn total, not the last call's usage.
        assert usage_events[0]["metadata"][USAGE_METADATA_KEY] == {
            "input_tokens": 2 * _INPUT_TOKENS_PER_CALL,
            "output_tokens": 2 * _OUTPUT_TOKENS_PER_CALL,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }


class TestUsageMapping:
    def test_usage_from_agent_maps_all_fields(self):
        class _Metrics:
            accumulated_usage = {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 3,
                "cacheWriteInputTokens": 2,
            }

        class _Agent:
            event_loop_metrics = _Metrics()

        usage = StrandsAdapter._usage_from_agent(cast("Any", _Agent()))

        assert usage == TurnUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=3,
            cache_write_tokens=2,
        )


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = StrandsAdapter(model="m")
        await adapter.on_cleanup("never-seen-room")  # must not raise

    @pytest.mark.asyncio
    async def test_cleanup_removes_room_history(self):
        room_id = "room-clean"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=ScriptedStrandsModel([_SEND_TURN]))
        await adapter.on_started("Bot", "A bot")
        await _run_message(adapter, tools, room_id)
        assert room_id in adapter._message_history

        await adapter.on_cleanup(room_id)

        assert room_id not in adapter._message_history
