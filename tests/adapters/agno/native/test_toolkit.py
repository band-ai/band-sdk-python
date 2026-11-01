"""Async toolkit selection observes native execution without mutating borrowed tools."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from agno.tools import Toolkit

from band.integrations.agno.entrypoints import observe_tool
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.native.bodies import NativeBody, lookup
from tests.adapters.agno.native.kit import NATIVE_NAME, NATIVE_RESULT, run_model_call


class TestNativeToolkit:
    @pytest.mark.parametrize(
        "sync_tools", [[], [lookup]], ids=["async-only", "override"]
    )
    async def test_async_selection_is_observed_and_keeps_the_original_unchanged(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
        sync_tools: list[Callable[..., str]],
    ) -> None:
        borrowed = Toolkit(
            tools=sync_tools, async_tools=[(native_body.coroutine, NATIVE_NAME)]
        )
        original = borrowed.get_async_functions()[NATIVE_NAME]
        observed = observe_tool(borrowed)
        native_body.release.set()

        call, results = await run_model_call(
            scoped_tools, observed.get_async_functions()[NATIVE_NAME]
        )

        assert call.error is None
        assert results[-1].content == NATIVE_RESULT
        native_recorder.assert_completed_tool(NATIVE_NAME)
        assert native_body.finished.is_set()
        assert borrowed.get_async_functions()[NATIVE_NAME] is original
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])
