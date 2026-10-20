from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("parlant.sdk")

from parlant.core.tools import ToolContext

from band.integrations.parlant.customtools import build_custom_tool
from band.integrations.parlant.sessiontools import set_session_tools
from band.integrations.parlant.tools import create_parlant_tools
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.integrations.parlant.activity.kit import LookupInput
from tests.integrations.parlant.helpers import ToolServer


class TestParlantActivity:
    @pytest.mark.parametrize("failed", [False, True])
    async def test_real_server_reports_only_calling_session_and_always_ends(
        self, plugin_server: ToolServer, failed: bool
    ) -> None:
        tools, other = FakeAgentTools(), FakeAgentTools()
        recorder, quiet = ActivityRecorder(), ActivityRecorder()
        tools._activity_observer = recorder
        other._activity_observer = quiet
        set_session_tools("calling", tools)
        set_session_tools("other", other)
        entered, release = asyncio.Event(), asyncio.Event()

        async def lookup(args: LookupInput) -> str:
            assert args.note == "private-argument"
            entered.set()
            await release.wait()
            if failed:
                raise ValueError("private-error")
            return "private-result"

        await plugin_server.enable(
            create_parlant_tools(custom_tools=[(LookupInput, lookup)])
        )
        call = asyncio.create_task(
            plugin_server.call(
                "lookup", session_id="calling", arguments={"note": "private-argument"}
            )
        )
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            assert recorder.events == [{"kind": "tool_call", "name": "lookup"}]
            assert not quiet.events
        finally:
            release.set()
        result = await call
        assert result == (
            "Error running lookup: private-error" if failed else "private-result"
        )
        recorder.assert_completed_tool("lookup")
        assert not tools.events_sent

    async def test_custom_handler_cancellation_ends_activity(self) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        set_session_tools("cancelled", tools)
        entered = asyncio.Event()

        async def lookup(args: LookupInput) -> str:
            entered.set()
            await asyncio.Event().wait()
            return "unreachable"

        entry = build_custom_tool((LookupInput, lookup))
        task = asyncio.create_task(
            entry.function(
                ToolContext(
                    agent_id="agent", session_id="cancelled", customer_id="user"
                ),
                note="private-argument",
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        recorder.assert_completed_tool("lookup")
