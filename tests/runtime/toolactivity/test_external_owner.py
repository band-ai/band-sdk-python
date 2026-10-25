"""External activity ownership suppresses only its scoped tool wrappers."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.toolactivity import suppress_tool_activity
from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder

SCOPE_FAILURE = "external activity owner failed"


class TestExternalActivityOwner:
    async def test_success_restores_wrapped_tool_reporting(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        with suppress_tool_activity():
            await tools.lookup_peers()
        assert recorder.events == []

        await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)

    async def test_error_restores_wrapped_tool_reporting(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        with (
            pytest.raises(RuntimeError, match=SCOPE_FAILURE),
            suppress_tool_activity(),
        ):
            await tools.lookup_peers()
            raise RuntimeError(SCOPE_FAILURE)
        assert recorder.events == []

        await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)

    async def test_cancellation_restores_wrapped_tool_reporting(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        with pytest.raises(asyncio.CancelledError), suppress_tool_activity():
            await tools.lookup_peers()
            task.cancel()
            await asyncio.Event().wait()
        task.uncancel()
        assert recorder.events == []

        await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)

    async def test_inner_scope_preserves_the_outer_owner(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        with suppress_tool_activity():
            with suppress_tool_activity():
                await tools.lookup_peers()
            await tools.lookup_peers()
        assert recorder.events == []

        await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)

    async def test_disabled_scope_preserves_existing_wrapped_tool_reporting(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        with suppress_tool_activity(enabled=False):
            await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)

    async def test_disabled_inner_scope_preserves_the_outer_owner(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        with suppress_tool_activity(), suppress_tool_activity(enabled=False):
            await tools.lookup_peers()
        assert recorder.events == []

        await tools.lookup_peers()
        recorder.assert_completed_tool(BandTool.LOOKUP_PEERS)
