"""Cancelling actual native coroutine execution releases its captured resource."""

from __future__ import annotations

import asyncio

import pytest
from agno.tools.function import Function

from band.runtime.activity import tool_call_activity
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.native.bodies import NativeBody
from tests.adapters.agno.native.kit import NATIVE_NAME, native_call


class TestNativeCoroutineCleanup:
    @pytest.mark.looptime
    async def test_cancelled_awaiter_runs_body_finally_and_revokes_effects(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
    ) -> None:
        async with native_call(
            scoped_tools,
            Function.from_callable(native_body.coroutine, name=NATIVE_NAME),
        ) as call:
            await native_body.entered.wait()
            await native_recorder.wait_for_detail(tool_call_activity(NATIVE_NAME))
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call

        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])
