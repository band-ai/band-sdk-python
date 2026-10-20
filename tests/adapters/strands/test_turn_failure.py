from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

import pytest

from tests.framework_conformance.turnprobes import turn_input

pytest.importorskip("strands", reason="strands extra not installed")


import boto3
from strands.models.bedrock import BedrockModel
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.content import Messages
from strands.types.exceptions import EventLoopException
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from band.adapters.strands import (
    StrandsAdapter,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from band.core.types import (
    USAGE_METADATA_KEY,
    Emit,
    MessageType,
    is_usage_event,
)
from band.runtime.tools import BandTool
from band.testing import (
    ErrorTurn,
    FakeAgentTools,
    ScriptedStrandsModel,
    TextTurn,
    ToolTurn,
    events_of_type,
    failure_reports,
    reported_failures,
)
from tests.adapters.strands.kit import (
    _INPUT_TOKENS_PER_CALL,
    ROOM,
    SEND_TURN,
    _make_msg,
    _run_message,
    _run_turn,
    _tool_results,
)
from tests.responsespeer import ResponsesPeer
from tests.strandskit import outline


class TestTurnFailure:
    class RecordingBedrockModel(BedrockModel):
        """Serialize real Bedrock requests, then supply scripted model events."""

        def __init__(self) -> None:
            super().__init__(
                boto_session=boto3.Session(
                    aws_access_key_id="test",
                    aws_secret_access_key="test",
                    region_name="us-east-1",
                ),
                model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            )
            self.requests: list[dict[str, Any]] = []
            self.scripted = ScriptedStrandsModel(
                [
                    ToolTurn(BandTool.NO_REPLY, {"reason": "FYI only"}),
                    SEND_TURN,
                    TextTurn(""),
                ]
            )

        async def stream(
            self,
            messages: Messages,
            tool_specs: list[ToolSpec] | None = None,
            system_prompt: str | None = None,
            **kwargs: Any,
        ) -> AsyncGenerator[StreamEvent, None]:
            self.requests.append(self.format_request(messages, tool_specs))
            async for event in self.scripted.stream(
                messages, tool_specs, system_prompt, **kwargs
            ):
                yield event

    @pytest.mark.asyncio
    @pytest.mark.parametrize("emit_execution", [False, True])
    async def test_bedrock_decline_preserves_followup_turn(
        self, emit_execution: bool
    ) -> None:
        model = self.RecordingBedrockModel()
        adapter = StrandsAdapter(
            llm=model, emit=Emit.TOOL_CALLS if emit_execution else []
        )
        await adapter.on_started("Bot", "A bot")
        first = FakeAgentTools(room_id=ROOM)
        await _run_turn(adapter, first)
        second = FakeAgentTools(room_id=ROOM)
        await adapter.on_event(
            replace(
                turn_input(second, msg=_make_msg(ROOM, "Please reply now.")),
                is_session_bootstrap=False,
                participants_msg="Bob joined",
                contacts_msg="New contact",
            )
        )

        assert outline(model.requests[1]["messages"]) == [
            "user: text([Tester]: Hello)",
            "assistant: toolUse(call-band_no_reply)",
            (
                "user: toolResult(call-band_no_reply, success) "
                "text([System]: Bob joined\n\n[System]: New contact\n\n"
                "[Tester]: Please reply now.)"
            ),
        ]
        assert first.turn.complete and second.turn.complete
        assert first.messages_sent == []
        second.assert_message_sent(content="hi", count=1)
        assert failure_reports(first) == failure_reports(second) == []

    @pytest.mark.asyncio
    async def test_responses_decline_preserves_followup_tool_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = ResponsesPeer(
            [
                ToolTurn(BandTool.NO_REPLY, {"reason": "FYI only"}),
                SEND_TURN,
                TextTurn(""),
            ]
        )
        monkeypatch.setattr("openai.AsyncOpenAI", peer.client)
        adapter = StrandsAdapter(
            llm=OpenAIResponsesModel(
                client_args={"api_key": "test"}, model_id="test-model"
            )
        )
        await adapter.on_started("Bot", "A bot")
        first = FakeAgentTools(room_id=ROOM)
        await _run_turn(adapter, first)
        second = FakeAgentTools(room_id=ROOM)
        await adapter.on_event(
            replace(
                turn_input(second, msg=_make_msg(ROOM, "Please reply now.")),
                is_session_bootstrap=False,
            )
        )

        assert peer.requests[1]["input"][2:] == [
            {
                "type": "function_call_output",
                "call_id": peer.CALL_ID,
                "output": '{"status": "no_reply"}',
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "[Tester]: Please reply now."}
                ],
            },
        ]
        assert first.turn.complete and second.turn.complete
        second.assert_message_sent(content="hi", count=1)
        assert failure_reports(first) == failure_reports(second) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fails", [False, True], ids=["declined", "failed-decline"])
    @pytest.mark.parametrize("emit_execution", [False, True])
    async def test_responses_decline_ends_only_a_successful_turn(
        self, monkeypatch: pytest.MonkeyPatch, fails: bool, emit_execution: bool
    ) -> None:
        tools = FakeAgentTools(room_id=ROOM)
        peer = ResponsesPeer(
            [
                ToolTurn(BandTool.NO_REPLY, {"reason": [] if fails else "FYI only"}),
                ToolTurn(BandTool.NO_REPLY, {"reason": "FYI only"}),
                ErrorTurn(RuntimeError("decline requested another model call")),
            ]
        )
        monkeypatch.setattr("openai.AsyncOpenAI", peer.client)
        adapter = StrandsAdapter(
            llm=OpenAIResponsesModel(
                client_args={"api_key": "test"}, model_id="test-model"
            ),
            emit=Emit.USAGE | Emit.TOOL_CALLS if emit_execution else Emit.USAGE,
        )
        await adapter.on_started("Bot", "A bot")

        await _run_turn(adapter, tools)

        assert len(peer.requests) == (2 if fails else 1)
        assert tools.turn.complete
        assert tools.messages_sent == []
        assert failure_reports(tools) == []
        if emit_execution:
            results = events_of_type(tools, MessageType.TOOL_RESULT)
            assert [
                json.loads(result["content"])["is_error"] for result in results
            ] == ([True, False] if fails else [False])
        (usage,) = [
            event for event in tools.events_sent if is_usage_event(event["metadata"])
        ]
        requests = 2 if fails else 1
        assert usage["metadata"][USAGE_METADATA_KEY]["input_tokens"] == (
            requests * peer.INPUT_TOKENS
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fails", [False, True], ids=["completed", "failed-stream"])
    async def test_responses_tool_events_usage_and_failure(
        self, tools: FakeAgentTools, monkeypatch: pytest.MonkeyPatch, fails: bool
    ) -> None:
        final = ErrorTurn(RuntimeError("provider down")) if fails else TextTurn("")
        peer = ResponsesPeer([SEND_TURN, final])
        monkeypatch.setattr("openai.AsyncOpenAI", peer.client)
        adapter = StrandsAdapter(
            llm=OpenAIResponsesModel(
                client_args={"api_key": "test"}, model_id="test-model"
            ),
            emit=Emit.TOOL_CALLS | Emit.USAGE,
        )
        await adapter.on_started("Bot", "A bot")

        if fails:
            with pytest.raises(EventLoopException, match="provider down"):
                await _run_turn(adapter, tools)
        else:
            await _run_turn(adapter, tools)

        tools.assert_message_sent(content="hi", count=1)
        (call,) = events_of_type(tools, MessageType.TOOL_CALL)
        (result,) = events_of_type(tools, MessageType.TOOL_RESULT)
        assert json.loads(call["content"])["tool_call_id"] == peer.CALL_ID
        result_data = json.loads(result["content"])
        assert json.loads(result_data.pop("output")) == {
            "id": "msg-0",
            "recipients": [{"handle": "tester", "id": "tester", "name": None}],
            "success": True,
        }
        assert result_data == {
            "name": SEND_TURN.name,
            "tool_call_id": peer.CALL_ID,
            "is_error": False,
        }
        (usage,) = [
            event for event in tools.events_sent if is_usage_event(event["metadata"])
        ]
        completed_calls = 1 if fails else 2
        assert usage["metadata"][USAGE_METADATA_KEY] == {
            "input_tokens": completed_calls * peer.INPUT_TOKENS,
            "output_tokens": completed_calls * peer.OUTPUT_TOKENS,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        assert failure_reports(tools) == (
            [("strands", GENERIC_PROVIDER_FAILURE_MESSAGE)] if fails else []
        )

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

        tools.assert_message_sent(content="hi", count=1)
        assert _tool_results(adapter)  # the completed call is still in the transcript
        usage = [e for e in tools.events_sent if is_usage_event(e["metadata"])]
        assert usage[0]["metadata"][USAGE_METADATA_KEY]["input_tokens"] == (
            _INPUT_TOKENS_PER_CALL
        )
        failure = reported_failures(tools)[0]
        assert failure["provider"] == "strands"
        assert failure["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
