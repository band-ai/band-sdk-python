"""ExplicitDecline behavior for Pydantic AI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from band.adapters.pydantic_ai import PydanticAIAdapter, _is_replayable_history_message
from band.core.types import Emit, PlatformMessage
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import BandTool, TurnEffect
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.pydanticai.kit import (
    DecliningModel,
    _assert_billed_responses,
    _emitted_tool_results,
    _history_tool_returns,
    _reply_arguments,
    _run_on_message,
    _started_adapter,
    _streamed_tool_calls,
    _tool_feedback,
    _tool_returns,
)


class TestExplicitDecline:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("request_limit", [1, 3])
    async def test_decline_ends_native_stream_on_last_allowed_request(
        self, request_limit: int
    ) -> None:
        model = DecliningModel()
        adapter = PydanticAIAdapter(llm=model.model)
        await adapter.on_started("Probe", "probe")
        tools = FakeAgentTools()
        results = []
        async with adapter._agent.run_stream_events(
            "go", deps=tools, usage_limits=UsageLimits(request_limit=request_limit)
        ) as events:
            async for event in events:
                if isinstance(event, AgentRunResultEvent):
                    results.append(event.result)

        assert model.requests == 1
        assert len(results) == 1
        assert results[0].output is None
        assert _tool_returns(results[0]) == [{"status": "no_reply"}]
        assert tools.turn.replied
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    async def test_adapter_preserves_decline_events_history_and_per_turn_usage(
        self, sample_message: PlatformMessage
    ) -> None:
        model = DecliningModel()
        adapter = PydanticAIAdapter(llm=model.model, emit=(Emit.TOOL_CALLS, Emit.USAGE))
        await adapter.on_started("Probe", "probe")
        tools = FakeAgentTools()
        await _run_on_message(adapter, sample_message, tools)

        assert model.requests == 1
        assert _emitted_tool_results(tools) == [
            (BandTool.NO_REPLY, "{'status': 'no_reply'}", "decline-1")
        ]
        prior = list(adapter._message_history[tools.room_id])
        assert _history_tool_returns(prior) == [
            (BandTool.NO_REPLY, "decline-1", {"status": "no_reply"})
        ]
        assert all(_is_replayable_history_message(message) for message in prior)
        _assert_billed_responses(tools, prior)
        assert reported_failures(tools) == []

        seen_history: list[ModelMessage] = []

        async def followup(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            if not seen_history:
                seen_history.extend(messages)
                yield {
                    0: DeltaToolCall(
                        name=BandTool.SEND_MESSAGE,
                        json_args=json.dumps(
                            _reply_arguments(sample_message, "A fresh reply")
                        ),
                    )
                }
            else:
                yield "done"

        next_tools = FakeAgentTools(room_id=tools.room_id)
        with adapter._agent.override(model=FunctionModel(stream_function=followup)):
            await adapter.on_message(
                msg=sample_message,
                tools=next_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id=tools.room_id,
            )

        assert _history_tool_returns(seen_history) == _history_tool_returns(prior)
        assert next_tools.chat[0] == "A fresh reply"
        new_history = adapter._message_history[tools.room_id][len(prior) :]
        _assert_billed_responses(next_tools, new_history, model_requests=2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "rejection",
        [RuntimeError("decline rejected"), {"ok": False}, None],
        ids=["raised", "returned_failure", "invalid_arguments"],
    )
    async def test_failed_decline_keeps_required_reply_executable(
        self, sample_message: PlatformMessage, rejection: Any
    ) -> None:
        tools = FakeAgentTools()
        if isinstance(rejection, Exception):
            tools.no_reply = AsyncMock(side_effect=rejection)
        elif rejection is not None:
            tools.no_reply = AsyncMock(return_value=rejection)
        decline_args = (
            {"unexpected": True} if rejection is None else {"reason": "decline"}
        )
        adapter = await _started_adapter()
        with adapter._agent.override(
            model=_streamed_tool_calls(
                (BandTool.NO_REPLY, decline_args),
                (
                    BandTool.SEND_MESSAGE,
                    _reply_arguments(sample_message, "Required reply"),
                ),
            )
        ):
            await _run_on_message(adapter, sample_message, tools)

        assert tools.chat == ["Required reply"]
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "first_tool", [BandTool.SEND_MESSAGE, BandTool.GET_PARTICIPANTS, "do_work"]
    )
    async def test_other_successful_tools_do_not_end_the_loop(
        self, sample_message: PlatformMessage, first_tool: str
    ) -> None:
        work_done = []

        @declares_turn_effect(TurnEffect.ACT)
        async def do_work() -> str:
            work_done.append("worked")
            return "worked"

        tools = FakeAgentTools()
        adapter = await _started_adapter(additional_tools=[do_work])
        arguments = (
            _reply_arguments(sample_message, "First reply")
            if first_tool == BandTool.SEND_MESSAGE
            else {}
        )
        with adapter._agent.override(
            model=_streamed_tool_calls(
                (first_tool, arguments),
                (
                    BandTool.SEND_MESSAGE,
                    _reply_arguments(sample_message, "Required next reply"),
                ),
            )
        ):
            await _run_on_message(adapter, sample_message, tools)

        assert tools.chat[-1] == "Required next reply"
        if first_tool == "do_work":
            assert work_done == ["worked"]
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    async def test_decline_preserves_every_result_in_its_tool_batch(
        self, sample_message: PlatformMessage
    ) -> None:
        async def batch(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[dict[int, DeltaToolCall]]:
            yield {
                0: DeltaToolCall(
                    name=BandTool.NO_REPLY, json_args="{}", tool_call_id="decline"
                ),
                1: DeltaToolCall(
                    name=BandTool.SEND_MESSAGE,
                    json_args=json.dumps(
                        _reply_arguments(sample_message, "Already sent")
                    ),
                    tool_call_id="reply",
                ),
                2: DeltaToolCall(
                    name=BandTool.GET_PARTICIPANTS, json_args="{}", tool_call_id="read"
                ),
            }

        adapter = PydanticAIAdapter(
            llm=FunctionModel(stream_function=batch), emit=Emit.TOOL_CALLS
        )
        await adapter.on_started("Probe", "probe")
        tools = FakeAgentTools()
        await _run_on_message(adapter, sample_message, tools)

        returns = _history_tool_returns(adapter._message_history[tools.room_id])
        assert {(name, call_id) for name, call_id, _ in returns} == {
            (BandTool.NO_REPLY, "decline"),
            (BandTool.SEND_MESSAGE, "reply"),
            (BandTool.GET_PARTICIPANTS, "read"),
        }
        assert {
            (name, call_id) for name, _, call_id in _emitted_tool_results(tools)
        } == {(name, call_id) for name, call_id, _ in returns}
        assert tools.chat == ["Already sent"]
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "shown"),
        [
            ("raised", "Error executing band_send_message: post failed"),
            ("returned_failure", "{'ok': False}"),
            ("invalid_arguments", "Invalid arguments for band_send_message"),
        ],
    )
    async def test_a_failed_sibling_keeps_the_decline_batch_open(
        self, sample_message: PlatformMessage, failure: str, shown: str
    ) -> None:
        """A successful decline leaves the loop running when another tool in the same response failed."""
        tools = FakeAgentTools()
        if failure != "invalid_arguments":
            send_message = tools.send_message

            async def fail_named_post(
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> Any:
                if content != "Will fail":
                    return await send_message(content, mentions)
                if failure == "raised":
                    raise RuntimeError("post failed")
                return {"ok": False}

            tools.send_message = fail_named_post

        requests = 0
        followup: list[ModelMessage] = []
        send_arguments: dict[str, Any] = (
            {"content": "Will fail", "mentions": "alice"}
            if failure == "invalid_arguments"
            else _reply_arguments(sample_message, "Will fail")
        )

        async def batch(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            nonlocal requests
            requests += 1
            if requests == 1:
                yield {
                    0: DeltaToolCall(
                        name=BandTool.NO_REPLY,
                        json_args=json.dumps({"reason": "Settled"}),
                        tool_call_id="decline",
                    ),
                    1: DeltaToolCall(
                        name=BandTool.SEND_MESSAGE,
                        json_args=json.dumps(send_arguments),
                        tool_call_id="reply",
                    ),
                }
                return
            if requests == 2:
                followup.extend(messages)
                yield {
                    0: DeltaToolCall(
                        name=BandTool.SEND_MESSAGE,
                        json_args=json.dumps(
                            _reply_arguments(sample_message, "Recovered")
                        ),
                        tool_call_id="recovered",
                    )
                }
                return
            yield ""

        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=batch))
        await adapter.on_started("Probe", "probe")
        await _run_on_message(adapter, sample_message, tools)

        assert requests == 3
        assert shown in _tool_feedback(followup)
        assert shown in _tool_feedback(adapter._message_history[tools.room_id])
        assert tools.chat == ["Recovered"]
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancel", [False, True], ids=["continue", "cancel"])
    async def test_concurrent_room_keeps_its_lifecycle(
        self, sample_message: PlatformMessage, cancel: bool
    ) -> None:
        entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def wait_for_work() -> str:
            entered.set()
            try:
                await release.wait()
                return "work completed"
            finally:
                cleaned.set()

        adapter = await _started_adapter(additional_tools=[wait_for_work])
        slow_tools, decline_tools = (
            FakeAgentTools(room_id="slow"),
            FakeAgentTools(room_id="decline"),
        )

        async def slow_room() -> None:
            with adapter._agent.override(
                model=_streamed_tool_calls(
                    ("wait_for_work", {}),
                    (
                        BandTool.SEND_MESSAGE,
                        _reply_arguments(sample_message, "Finished work"),
                    ),
                )
            ):
                await _run_on_message(adapter, sample_message, slow_tools)

        async with asyncio.TaskGroup() as group:
            task = group.create_task(slow_room())
            try:
                await entered.wait()
                with adapter._agent.override(model=DecliningModel().model):
                    await _run_on_message(adapter, sample_message, decline_tools)
                assert not task.done()
                if cancel:
                    task.cancel()
            finally:
                release.set()

        assert cleaned.is_set()
        assert task.cancelled() is cancel
        assert slow_tools.chat == ([] if cancel else ["Finished work"])
        assert decline_tools.chat == []
        assert reported_failures(slow_tools) == reported_failures(decline_tools) == []
