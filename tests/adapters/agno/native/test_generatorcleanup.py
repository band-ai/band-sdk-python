"""Partially consumed native generators retain finally cleanup after revocation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from operator import attrgetter
from typing import Any

import pytest
from agno.tools.function import Function, FunctionCall

from band.integrations.agno.activity import observing_run
from band.integrations.agno.entrypoints import observe_function
from band.runtime.activity import tool_call_activity
from band.runtime.cycle import TurnScope
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.helpers import CapturingModel
from tests.adapters.agno.native.bodies import (
    NativeBody,
    advance_async_generator,
    advance_generator,
    close_async_generator,
    close_generator,
)
from tests.adapters.agno.native.kit import NATIVE_NAME, NATIVE_RESULT


@pytest.mark.parametrize(
    ("entrypoint", "advance", "close"),
    [
        (attrgetter("generator"), advance_generator, close_generator),
        (
            attrgetter("async_generator"),
            advance_async_generator,
            close_async_generator,
        ),
    ],
    ids=["generator", "async-generator"],
)
class TestNativeGeneratorCleanup:
    async def test_closing_a_partial_result_runs_the_actual_body_finally(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
        entrypoint: Callable[[NativeBody], Callable[..., Any]],
        advance: Callable[[Any], Awaitable[str]],
        close: Callable[[Any], Awaitable[None]],
    ) -> None:
        function = observe_function(
            Function.from_callable(entrypoint(native_body), name=NATIVE_NAME)
        )
        call = FunctionCall(function=function, arguments={}, call_id="native")
        async with observing_run(scoped_tools):
            await CapturingModel().arun_function_call(call)
            try:
                assert await advance(call.result) == NATIVE_RESULT
                await native_recorder.wait_for_detail(tool_call_activity(NATIVE_NAME))
                assert not native_body.finished.is_set()
            finally:
                await close(call.result)

        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])

    async def test_revoked_result_still_closes_its_original_body(
        self,
        scoped_tools: AgentTools,
        native_scope: TurnScope,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
        entrypoint: Callable[[NativeBody], Callable[..., Any]],
        advance: Callable[[Any], Awaitable[str]],
        close: Callable[[Any], Awaitable[None]],
    ) -> None:
        function = observe_function(
            Function.from_callable(entrypoint(native_body), name=NATIVE_NAME)
        )
        call = FunctionCall(function=function, arguments={}, call_id="native")
        async with observing_run(scoped_tools):
            await CapturingModel().arun_function_call(call)
            try:
                assert await advance(call.result) == NATIVE_RESULT
                await native_recorder.wait_for_detail(tool_call_activity(NATIVE_NAME))
                native_scope.terminate()
                assert not native_body.finished.is_set()
                with pytest.raises(asyncio.CancelledError):
                    await native_body.tools.send_notice("stale", mentions=["@Alice"])
                with pytest.raises(asyncio.CancelledError):
                    await advance(call.result)
            finally:
                await close(call.result)

        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])
