"""Unit tests for the Strands adapter (scripted model, no live inference).

Turn dispatch through the framework's own agent loop is pinned by
tests/framework_conformance/test_strands_injection_spike.py; these tests cover
the adapter's own state: history, injected context, terminal-action policy,
usage, and cleanup.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any, cast

import pytest
from pydantic import BaseModel

from tests.strandskit import text, tool_call, tool_result

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool  # noqa: E402
from strands.models.openai import OpenAIModel  # noqa: E402
from strands.types.content import Messages  # noqa: E402
from strands.types.exceptions import EventLoopException  # noqa: E402
from strands.types.streaming import StreamEvent  # noqa: E402
from strands.types.tools import ToolChoice, ToolSpec  # noqa: E402

from band.adapters.strands import CustomToolBridge, StrandsAdapter  # noqa: E402
from band.converters.strands import StrandsHistoryConverter  # noqa: E402
from band.core.protocols import AgentToolsProtocol  # noqa: E402
from band.core.types import (  # noqa: E402
    AgentInput,
    Capability,
    Emit,
    HistoryProvider,
    PlatformMessage,
    TurnUsage,
)
from band.testing import (  # noqa: E402
    ErrorTurn,
    FakeAgentTools,
    ScriptedStrandsModel,
    ScriptedTurn,
    ToolTurn,
)

_INPUT_TOKENS_PER_CALL = 7
_OUTPUT_TOKENS_PER_CALL = 3

ROOM = "room-1"
SEND_TURN = ToolTurn("band_send_message", {"content": "hi", "mentions": ["@tester"]})


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


@pytest.fixture
def tools() -> FakeAgentTools:
    return FakeAgentTools(room_id=ROOM)


@pytest.fixture
def scripted() -> Callable[..., Awaitable[StrandsAdapter]]:
    """Build a started adapter whose model replays the given turns."""

    async def build(
        *turns: ScriptedTurn,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **adapter_args: Any,
    ) -> StrandsAdapter:
        adapter = StrandsAdapter(
            model=ScriptedStrandsModel(
                turns, input_tokens=input_tokens, output_tokens=output_tokens
            ),
            **adapter_args,
        )
        await adapter.on_started("Bot", "A bot")
        return adapter

    return build


async def _run_message(
    adapter: StrandsAdapter,
    tools: FakeAgentTools,
    room_id: str = ROOM,
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


def _tool_results(adapter: StrandsAdapter, room_id: str = ROOM) -> list[str]:
    """The tool outputs the model saw this turn, in order."""
    return [
        item["text"]
        for message in adapter._message_history[room_id]
        for block in message["content"]
        if "toolResult" in block
        for item in block["toolResult"]["content"]
        if "text" in item
    ]


def _alternates(history: list) -> bool:
    """Whether the transcript never puts two same-role turns in a row."""
    roles = [message["role"] for message in history]
    return all(first != second for first, second in zip(roles, roles[1:]))


def _errors(tools: FakeAgentTools) -> list[str]:
    return [e["content"] for e in tools.events_sent if e["message_type"] == "error"]


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
            capabilities=Capability.MEMORY | Capability.CONTACTS,
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

    @pytest.mark.asyncio
    async def test_excluded_tools_never_reach_the_model(self):
        """Reaching a tool is enough to execute it, so a filter must apply here."""
        adapter = StrandsAdapter(
            model="m",
            exclude_tools=["band_remove_participant"],
        )
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}

        assert "band_remove_participant" not in names
        assert "band_send_message" in names

    @pytest.mark.asyncio
    async def test_each_turns_tools_own_their_input_schema(self):
        """Strands normalizes a tool spec by writing into its nested schema.

        Tools are rebuilt per turn, so a schema shared between turns would carry
        one turn's framework normalization into the next.
        """
        adapter = StrandsAdapter(model="m")
        await adapter.on_started("Bot", "A bot")

        def send_properties(turn_tools: list) -> dict:
            by_name = {tool.tool_name: tool for tool in turn_tools}
            return by_name["band_send_message"].tool_spec["inputSchema"]["json"][
                "properties"
            ]

        first = send_properties(adapter._build_platform_tools(FakeAgentTools()))
        second = send_properties(adapter._build_platform_tools(FakeAgentTools()))
        first["content"]["normalized"] = "written by the framework"

        assert "normalized" not in second["content"]


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


class TestOpenAIRehydration:
    """Cold-boot history remains valid when it reaches OpenAI."""

    _HISTORY = [
        tool_call("calc", {"expr": "2+2"}, "call-1"),
        text("also, hello"),
        tool_result("calc", "4", "call-1"),
    ]

    class RecordingOpenAIModel(OpenAIModel):
        """Run the real OpenAI serializer, then answer from the offline model."""

        def __init__(self) -> None:
            super().__init__(client_args={"api_key": "test"}, model_id="gpt-4o-mini")
            self.requests: list[list[dict[str, Any]]] = []
            self._scripted = ScriptedStrandsModel([SEND_TURN])

        async def stream(
            self,
            messages: Messages,
            tool_specs: list[ToolSpec] | None = None,
            system_prompt: str | None = None,
            *,
            tool_choice: ToolChoice | None = None,
            **kwargs: Any,
        ) -> AsyncGenerator[StreamEvent, None]:
            self.requests.append(self.format_request_messages(messages, system_prompt))
            async for event in self._scripted.stream(
                messages,
                tool_specs,
                system_prompt,
                tool_choice=tool_choice,
                **kwargs,
            ):
                yield event

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "history_converter",
        [None, StrandsHistoryConverter()],
        ids=["default-converter", "custom-converter"],
    )
    async def test_openai_serialization_keeps_the_tool_result_adjacent(
        self, history_converter, tools
    ):
        model = self.RecordingOpenAIModel()
        adapter = StrandsAdapter(
            model=model,
            history_converter=history_converter,
        )
        await adapter.on_started("Bot", "A bot")

        await adapter.on_event(
            AgentInput(
                msg=_make_msg(ROOM),
                tools=cast("AgentToolsProtocol", tools),
                history=HistoryProvider(raw=self._HISTORY),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=ROOM,
            )
        )

        tool_call_index = next(
            index
            for index, message in enumerate(model.requests[0])
            if "tool_calls" in message
        )
        assert model.requests[0][tool_call_index : tool_call_index + 3] == [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "arguments": '{"expr": "2+2"}',
                            "name": "calc",
                        },
                        "id": "call-1",
                        "type": "function",
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "4"},
            {
                "role": "user",
                "content": [{"text": "[Alice]: also, hello", "type": "text"}],
            },
        ]


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_send_message_turn_dispatches_and_persists_history(
        self, tools, scripted
    ):
        adapter = await scripted(SEND_TURN)

        await _run_message(adapter, tools)

        tools.assert_message_sent(content="hi", mentions=["@tester"], count=1)
        # user prompt + toolUse + toolResult + final text
        assert len(adapter._message_history[ROOM]) == 4

    @pytest.mark.asyncio
    async def test_bootstrap_rehydrates_history(self, tools, scripted):
        adapter = await scripted(SEND_TURN)
        prior = [
            {"role": "user", "content": [{"text": "[Tester]: earlier question"}]},
            {"role": "assistant", "content": [{"text": "earlier answer"}]},
        ]

        await _run_message(adapter, tools, history=list(prior))

        persisted = adapter._message_history[ROOM]
        assert persisted[:2] == prior
        assert len(persisted) > 2  # this turn appended on top

    @pytest.mark.asyncio
    async def test_later_turns_keep_the_transcript_the_adapter_owns(
        self, tools, scripted
    ):
        """Only a session bootstrap reseeds from platform history.

        A later turn that reseeded would replay the room's own transcript on top
        of the one the adapter is already holding.
        """
        adapter = await scripted(SEND_TURN, SEND_TURN)
        await _run_message(adapter, tools, history=[])
        after_first = list(adapter._message_history[ROOM])

        await _run_message(adapter, tools, history=[], is_session_bootstrap=False)

        assert adapter._message_history[ROOM][: len(after_first)] == after_first

    @pytest.mark.asyncio
    async def test_platform_context_rides_the_turn_it_belongs_to(self, tools, scripted):
        """Strands appends the prompt itself, so context posted as its own
        message would leave two user turns in a row — rejected by Converse."""
        adapter = await scripted(SEND_TURN)

        await _run_message(
            adapter,
            tools,
            participants_msg="Alice joined",
            contacts_msg="Bob is now a contact",
        )

        history = adapter._message_history[ROOM]
        assert history[0]["content"][0]["text"].startswith(
            "[System]: Alice joined\n\n[System]: Bob is now a contact\n\n"
        )
        assert _alternates(history), [message["role"] for message in history]

    @pytest.mark.asyncio
    async def test_narration_failure_does_not_cost_the_room_its_reply(self, scripted):
        """Execution events are best-effort; a flaky event backend must not end the turn."""

        class NoEventTools(FakeAgentTools):
            async def send_event(self, *args, **kwargs):
                raise RuntimeError("events down")

        tools = NoEventTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN, emit=Emit.TOOL_CALLS)

        await _run_message(adapter, tools)

        tools.assert_message_sent(content="hi", count=1)

    @pytest.mark.asyncio
    async def test_usage_emitted_once_per_turn(self, tools, scripted):
        adapter = await scripted(
            SEND_TURN,
            input_tokens=_INPUT_TOKENS_PER_CALL,
            output_tokens=_OUTPUT_TOKENS_PER_CALL,
            emit=Emit.USAGE,
        )

        await _run_message(adapter, tools)

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


class TestTurnProductivity:
    """A turn that reached the room ends quietly; anything else is reported."""

    @pytest.mark.asyncio
    async def test_failed_band_tool_is_not_terminal(self, scripted):
        """A platform tool that raised did no productive work, however it is reported."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        tools = FailingTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN)

        await _run_message(adapter, tools)

        assert tools.messages_sent == []
        assert len(_errors(tools)) == 1
        # The shared bridge returns a normalized, model-visible tool failure.
        assert any(
            text.startswith("Error executing band_send_message:")
            for text in _tool_results(adapter)
        )

    @pytest.mark.asyncio
    async def test_a_failure_replays_as_a_failure_after_a_restart(self, scripted):
        """The persisted event is all a restart has; without is_error the
        converter would tell the model the failed operation succeeded."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        tools = FailingTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN, emit=Emit.TOOL_CALLS)

        await _run_message(adapter, tools)

        rehydrated = StrandsHistoryConverter(agent_name="Bot").convert(
            [
                {"role": "assistant", "content": event["content"], **event}
                for event in tools.events_sent
                if event["message_type"] in ("tool_call", "tool_result")
            ]
        )
        results = [
            block["toolResult"]
            for message in rehydrated
            for block in message["content"]
            if "toolResult" in block
        ]
        assert [result["status"] for result in results] == ["error"]

    @pytest.mark.asyncio
    async def test_read_only_tool_alone_does_not_end_the_turn(self, tools, scripted):
        """Looking peers up succeeds but posts nothing, so the reply is still missing."""
        adapter = await scripted(ToolTurn("band_lookup_peers", {}))

        await _run_message(adapter, tools)

        assert _tool_results(adapter)  # the lookup did run and succeed
        assert tools.messages_sent == []
        assert "band_send_message" in _errors(tools)[0]

    @pytest.mark.asyncio
    async def test_invalid_tool_arguments_are_answered_not_raised(
        self, tools, scripted
    ):
        """A malformed call is the model's mistake to correct, not a turn-ending crash."""
        adapter = await scripted(ToolTurn("band_send_message", {"mentions": ["@x"]}))

        await _run_message(adapter, tools)

        assert tools.messages_sent == []
        assert _tool_results(adapter) == [
            "Invalid arguments for band_send_message: content: Field required"
        ]

    @pytest.mark.asyncio
    async def test_custom_tool_failure_is_reported_to_the_model(self, tools, scripted):
        class BoomInput(BaseModel):
            """Explode on demand."""

            note: str

        async def boom(args: BoomInput) -> str:
            raise RuntimeError("no network")

        adapter = await scripted(
            ToolTurn("boom", {"note": "go"}), additional_tools=[(BoomInput, boom)]
        )

        await _run_message(adapter, tools)

        assert _tool_results(adapter) == ["Error executing tool 'boom': no network"]


class TestTurnFailure:
    @pytest.mark.asyncio
    async def test_provider_failure_keeps_the_transcript_and_reports_usage(
        self, tools, scripted
    ):
        """The turn dies, but the work it already did must survive it.

        Dropping the transcript would lose the tool call the room already saw,
        and dropping usage would under-report the calls that were paid for.
        """
        adapter = await scripted(
            SEND_TURN,
            ErrorTurn(RuntimeError("provider down")),
            input_tokens=_INPUT_TOKENS_PER_CALL,
            emit=Emit.USAGE,
        )

        with pytest.raises(EventLoopException, match="provider down"):
            await _run_message(adapter, tools)

        from band.core.types import USAGE_METADATA_KEY, is_usage_event

        tools.assert_message_sent(content="hi", count=1)
        assert _tool_results(adapter)  # the completed call is still in the transcript
        usage = [e for e in tools.events_sent if is_usage_event(e["metadata"])]
        assert usage[0]["metadata"][USAGE_METADATA_KEY]["input_tokens"] == (
            _INPUT_TOKENS_PER_CALL
        )


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
    async def test_cleanup_removes_room_history(self, tools, scripted):
        adapter = await scripted(SEND_TURN)
        await _run_message(adapter, tools)
        assert ROOM in adapter._message_history

        await adapter.on_cleanup(ROOM)

        assert ROOM not in adapter._message_history
