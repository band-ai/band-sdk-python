"""A custom tool bound to a room shows its name while its handler runs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.exceptions import InvalidToolArgumentsError
from band.runtime.custom_tools import execute_custom_tool
from band.runtime.tools import AgentTools
from band.testing.activity import ActivityRecorder, shown_tool
from tests.integrations.activitykit import LookupInput

LOOKUP_FAILURE = "lookup service unavailable"


class TestCustomToolActivity:
    async def test_the_tool_shows_until_its_handler_returns(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def lookup(inp: LookupInput) -> str:
            entered.set()
            await release.wait()
            return f"{inp.note}: a definition"

        call = asyncio.create_task(
            execute_custom_tool((LookupInput, lookup), {"note": "x"}, tools=tools)
        )
        try:
            await entered.wait()
            assert recorder.events == [shown_tool("lookup")]
        finally:
            release.set()
            result = await call

        assert result == "x: a definition"
        recorder.assert_completed_tool("lookup")

    async def test_a_failing_handler_still_ends_the_activity(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        async def lookup(inp: LookupInput) -> str:
            raise RuntimeError(LOOKUP_FAILURE)

        with pytest.raises(RuntimeError, match=LOOKUP_FAILURE):
            await execute_custom_tool((LookupInput, lookup), {"note": "x"}, tools=tools)

        recorder.assert_completed_tool("lookup")

    async def test_band_tools_the_handler_calls_show_as_the_custom_tool(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        shown_while_peers_looked_up: list[dict[str, Any]] = []

        async def lookup(inp: LookupInput) -> str:
            await tools.lookup_peers()
            shown_while_peers_looked_up.extend(recorder.events)
            return inp.note

        await execute_custom_tool((LookupInput, lookup), {"note": "x"}, tools=tools)

        assert shown_while_peers_looked_up == [shown_tool("lookup")]
        recorder.assert_completed_tool("lookup")

    async def test_rejected_arguments_show_nothing(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        async def lookup(inp: LookupInput) -> str:
            return inp.note

        with pytest.raises(InvalidToolArgumentsError):
            await execute_custom_tool((LookupInput, lookup), {}, tools=tools)

        assert recorder.events == []
