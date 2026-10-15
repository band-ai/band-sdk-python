from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any, ClassVar, cast

import pytest

from tests.framework_conformance.turnprobes import turn_input
from tests.strandskit import text, tool_call, tool_result

pytest.importorskip("strands", reason="strands extra not installed")


from strands.models.openai import OpenAIModel
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from band.adapters.strands import (
    StrandsAdapter,
)
from band.converters.strands import StrandsHistoryConverter
from band.core.protocols import (
    AgentToolsProtocol,
)
from band.core.types import (
    AgentInput,
    HistoryProvider,
)
from band.testing import (
    FakeAgentTools,
    ScriptedStrandsModel,
    TextTurn,
)
from tests.adapters.strands.kit import (
    ROOM,
    SEND_TURN,
    _make_msg,
)
from tests.responsespeer import ResponsesPeer


class TestOpenAIRehydration:
    """Cold-boot history remains valid when it reaches OpenAI."""

    _HISTORY: ClassVar[list[dict]] = [
        tool_call("calc", {"expr": "2+2"}, "call-1"),
        text("also, hello"),
        tool_result("calc", "4", "call-1"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("history_converter", [None, StrandsHistoryConverter()])
    async def test_responses_rehydrates_tool_results_before_user_text(
        self,
        history_converter: StrandsHistoryConverter | None,
        tools: FakeAgentTools,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        peer = ResponsesPeer([SEND_TURN, TextTurn("")])
        monkeypatch.setattr("openai.AsyncOpenAI", peer.client)
        model = OpenAIResponsesModel(
            client_args={"api_key": "test"}, model_id="test-model"
        )
        adapter = StrandsAdapter(llm=model, history_converter=history_converter)
        await adapter.on_started("Bot", "A bot")
        inp = replace(
            turn_input(tools, msg=_make_msg(ROOM)),
            history=HistoryProvider(raw=self._HISTORY),
        )

        await adapter.on_event(inp)

        assert peer.requests[0]["input"][:3] == [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "calc",
                "arguments": '{"expr": "2+2"}',
            },
            {"type": "function_call_output", "call_id": "call-1", "output": "4"},
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "[Alice]: also, hello"}],
            },
        ]
        tools.assert_message_sent(content="hi", count=1)

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
        adapter = StrandsAdapter(llm=model, history_converter=history_converter)
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
