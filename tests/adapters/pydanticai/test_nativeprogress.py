"""Native run progress remains observable without logging room contents."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from band.adapters.pydantic_ai import (
    PydanticAIAdapter,
    _log_native_progress,
    _native_turn,
)
from band.core.types import Emit, PlatformMessage
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, failure_reports, reported_failures
from tests.adapters.pydanticai.kit import (
    _assert_billed_responses,
    _reply_arguments,
    _run_on_message,
    _streamed_tool_calls,
)


class TestNativeDiagnostics:
    @pytest.mark.asyncio
    async def test_initial_acknowledgement_then_decline_finishes_without_replay(
        self, sample_message: PlatformMessage, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests = 0

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[dict[int, DeltaToolCall]]:
            nonlocal requests
            requests += 1
            replying = requests == 1
            yield {
                0: DeltaToolCall(
                    name=BandTool.SEND_MESSAGE if replying else BandTool.NO_REPLY,
                    json_args=json.dumps(
                        _reply_arguments(sample_message, "Noted")
                        if replying
                        else {"reason": "Already acknowledged"}
                    ),
                )
            }

        caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")
        tools = FakeAgentTools(room_id=sample_message.room_id)
        adapter = PydanticAIAdapter(
            llm=FunctionModel(stream_function=stream), emit=Emit.USAGE
        )
        await _run_on_message(adapter, sample_message, tools)

        assert requests == 2
        assert tools.chat == ["Noted"]
        assert reported_failures(tools) == []
        assert "phase=run_completed" in caplog.text
        _assert_billed_responses(
            tools, adapter._message_history[tools.room_id], model_requests=2
        )

    @pytest.mark.asyncio
    async def test_streamed_tool_turn_keeps_effects_and_redacts_payloads(
        self, sample_message: PlatformMessage, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")
        message = replace(sample_message, content="private prompt body")
        tools = FakeAgentTools(room_id=message.room_id)
        adapter = PydanticAIAdapter(
            llm=_streamed_tool_calls(
                (
                    BandTool.SEND_MESSAGE,
                    {"content": "private tool body", "mentions": ["Alice"]},
                )
            )
        )

        await _run_on_message(adapter, message, tools)

        tools.assert_message_sent(content="private tool body")
        assert caplog.text.count("phase=request_attempt") == 2
        assert caplog.text.count("phase=response_ready") == 2
        assert "phase=tool_succeeded" in caplog.text
        assert f"tool='{BandTool.SEND_MESSAGE}'" in caplog.text
        assert f"room={message.room_id} message={message.id}" in caplog.text
        assert (
            caplog.text.index("phase=result_received")
            < caplog.text.index("phase=iterator_completed")
            < caplog.text.index("phase=run_completed")
        )
        assert "private prompt body" not in caplog.text
        assert "private tool body" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "yield_first", [False, True], ids=["before-first", "midstream"]
    )
    async def test_stream_error_is_observed_and_preserves_exception(
        self,
        sample_message: PlatformMessage,
        caplog: pytest.LogCaptureFixture,
        yield_first: bool,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")
        failure = RuntimeError("private provider error body")

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str]:
            if yield_first:
                yield "private response body"
            raise failure

        tools = FakeAgentTools(room_id=sample_message.room_id)
        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))

        with pytest.raises(RuntimeError) as raised:
            await _run_on_message(adapter, sample_message, tools)

        assert raised.value is failure
        assert "phase=run_error" in caplog.text
        assert "error=RuntimeError" in caplog.text
        assert "phase=response_ready" not in caplog.text
        assert "phase=run_completed" not in caplog.text
        assert "private provider error body" not in caplog.text
        assert "private response body" not in caplog.text
        assert len(failure_reports(tools)) == 1

    @pytest.mark.asyncio
    async def test_cancellation_closes_native_stream_without_reporting_failure(
        self, sample_message: PlatformMessage, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")
        blocked = asyncio.Event()
        closed = asyncio.Event()
        release = asyncio.Event()

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str]:
            try:
                yield "private response body"
                blocked.set()
                await release.wait()
            finally:
                closed.set()

        tools = FakeAgentTools(room_id=sample_message.room_id)
        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))
        async with asyncio.TaskGroup() as tasks:
            task = tasks.create_task(_run_on_message(adapter, sample_message, tools))
            await asyncio.wait_for(blocked.wait(), timeout=5)
            task.cancel()

        assert task.cancelled()
        assert closed.is_set()
        assert "phase=stream_progress" in caplog.text
        assert "phase=run_cancelled" in caplog.text
        assert "error=CancelledError" in caplog.text
        assert "phase=response_ready" not in caplog.text
        assert "phase=run_completed" not in caplog.text
        assert "private response body" not in caplog.text
        assert not failure_reports(tools)

    @pytest.mark.asyncio
    async def test_long_stream_reports_bounded_progress(
        self, sample_message: PlatformMessage, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str]:
            for _ in range(128):
                yield "private token"

        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))
        await _run_on_message(
            adapter, sample_message, FakeAgentTools(room_id=sample_message.room_id)
        )

        assert 1 < caplog.text.count("phase=stream_progress") <= 9
        assert "phase=run_completed" in caplog.text
        assert "private token" not in caplog.text

    def test_progress_lines_are_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """Boundary traces stay off the default INFO log."""
        token = _native_turn.set(("room-1", "msg-1", 0.0))
        try:
            caplog.set_level(logging.INFO, logger="band.adapters.pydantic_ai")
            _log_native_progress("run_completed")
            assert "phase=run_completed" not in caplog.text
            caplog.set_level(logging.DEBUG, logger="band.adapters.pydantic_ai")
            _log_native_progress("run_completed")
        finally:
            _native_turn.reset(token)
        assert "phase=run_completed" in caplog.text
