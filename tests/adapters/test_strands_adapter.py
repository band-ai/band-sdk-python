"""Unit tests for the Strands adapter (mocked model, no live inference)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, cast

import pytest
from pydantic import BaseModel

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool  # noqa: E402
from strands.models import Model  # noqa: E402
from strands.types.content import Messages  # noqa: E402
from strands.types.streaming import StreamEvent  # noqa: E402
from strands.types.tools import ToolSpec  # noqa: E402

from band.adapters.strands import StrandsAdapter, _CustomToolBridge  # noqa: E402
from band.converters.strands import StrandsHistoryConverter  # noqa: E402
from band.core.protocols import AgentToolsProtocol  # noqa: E402
from band.core.types import (  # noqa: E402
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
    TurnUsage,
)
from band.testing.fake_tools import FakeAgentTools  # noqa: E402


class _ScriptedModel(Model):
    """Replays scripted ("tool", name, args) / ("text", body) decisions."""

    def __init__(self, turns: list[Any]):
        self._turns = list(turns)
        self._config: dict[str, Any] = {}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(
        self,
        output_model: Any,
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError
        yield {}  # pragma: no cover - makes this an async generator

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        decision = self._turns.pop(0) if self._turns else ("text", "done")
        yield {"messageStart": {"role": "assistant"}}
        if decision[0] == "tool":
            _, name, args = decision
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": f"call-{name}", "name": name}}
                }
            }
            yield {
                "contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": decision[1]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
                "metrics": {"latencyMs": 1},
            }
        }


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


_SEND_TURN = (
    "tool",
    "band_send_message",
    {"content": "hi", "mentions": ["@tester"]},
)


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
        assert isinstance(bridge, _CustomToolBridge)
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

        names = {t.tool_name for t in adapter._strands_tools}
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

        names = {t.tool_name for t in adapter._strands_tools}
        assert {"band_list_contacts", "band_respond_contact_request"} <= names
        assert {"band_store_memory", "band_archive_memory"} <= names

    @pytest.mark.asyncio
    async def test_platform_tool_descriptions_from_registry(self):
        from band.runtime.tools import get_tool_description

        adapter = StrandsAdapter(model="m")
        await adapter.on_started("Bot", "A bot")

        by_name = {t.tool_name: t for t in adapter._strands_tools}
        assert by_name["band_send_message"].tool_spec[
            "description"
        ] == get_tool_description("band_send_message")


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_send_message_turn_dispatches_and_persists_history(self):
        room_id = "room-1"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=_ScriptedModel([_SEND_TURN, ("text", "done")]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        tools.assert_message_sent(content="hi", mentions=["@tester"], count=1)
        # user prompt + toolUse + toolResult + final text
        assert len(adapter._message_history[room_id]) == 4

    @pytest.mark.asyncio
    async def test_bootstrap_rehydrates_history(self):
        room_id = "room-rehydrate"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=_ScriptedModel([_SEND_TURN, ("text", "done")]))
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
        adapter = StrandsAdapter(model=_ScriptedModel([_SEND_TURN, ("text", "done")]))
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
    async def test_no_terminal_action_reports_error(self):
        room_id = "room-noop"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(model=_ScriptedModel([("text", "plain answer")]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        errors = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(errors) == 1
        assert "band_send_message" in errors[0]["content"]

    @pytest.mark.asyncio
    async def test_failed_band_tool_is_not_terminal(self):
        """A platform tool whose wrapper returns "Error ..." does not end the turn productively."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        room_id = "room-fail"
        tools = FailingTools(room_id=room_id)
        adapter = StrandsAdapter(model=_ScriptedModel([_SEND_TURN, ("text", "done")]))
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        assert tools.messages_sent == []
        errors = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(errors) == 1
        # The failed call is visible to the model as an "Error ..." tool result.
        result_texts = [
            item["text"]
            for message in adapter._message_history[room_id]
            for block in message["content"]
            if "toolResult" in block
            for item in block["toolResult"]["content"]
            if "text" in item
        ]
        assert any(t.startswith("Error sending message:") for t in result_texts)

    @pytest.mark.asyncio
    async def test_usage_emitted_once_per_turn(self):
        room_id = "room-usage"
        tools = FakeAgentTools(room_id=room_id)
        adapter = StrandsAdapter(
            model=_ScriptedModel([_SEND_TURN, ("text", "done")]),
            features=AdapterFeatures(emit={Emit.USAGE}),
        )
        await adapter.on_started("Bot", "A bot")

        await _run_message(adapter, tools, room_id)

        from band.core.types import USAGE_METADATA_KEY, is_usage_event

        usage_events = [e for e in tools.events_sent if is_usage_event(e["metadata"])]
        assert len(usage_events) == 1
        # Two scripted model calls of 7/3 each -> the turn total, not the last call.
        assert usage_events[0]["metadata"][USAGE_METADATA_KEY] == {
            "input_tokens": 14,
            "output_tokens": 6,
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
        adapter = StrandsAdapter(model=_ScriptedModel([_SEND_TURN, ("text", "done")]))
        await adapter.on_started("Bot", "A bot")
        await _run_message(adapter, tools, room_id)
        assert room_id in adapter._message_history

        await adapter.on_cleanup(room_id)

        assert room_id not in adapter._message_history
