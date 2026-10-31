"""Concurrent calls with the same name retain independent activity owners."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.activity import REASONING, tool_call_activity
from band.runtime.custom_tools import execute_custom_tool
from band.runtime.tools import AgentTools
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.activitykit import LookupInput


class TestOverlappingTools:
    @pytest.mark.parametrize(
        ("finished", "remaining"),
        [("first", "second"), ("second", "first")],
        ids=["older-first", "newer-first"],
    )
    async def test_one_call_finishing_does_not_end_the_other(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        finished: str,
        remaining: str,
    ) -> None:
        entered = {name: asyncio.Event() for name in ("first", "second")}
        release = {name: asyncio.Event() for name in entered}

        async def lookup(inp: LookupInput) -> str:
            entered[inp.note].set()
            await release[inp.note].wait()
            return inp.note

        first = asyncio.create_task(
            execute_custom_tool((LookupInput, lookup), {"note": "first"}, tools=tools)
        )
        tasks = {"first": first}
        try:
            await entered["first"].wait()
            tasks["second"] = asyncio.create_task(
                execute_custom_tool(
                    (LookupInput, lookup), {"note": "second"}, tools=tools
                )
            )
            await entered["second"].wait()
            release[finished].set()
            assert await tasks[finished] == finished
            assert recorder.activity.detail == tool_call_activity("lookup")
            assert not tasks[remaining].done()
            release[remaining].set()
            assert await tasks[remaining] == remaining
            assert recorder.activity.detail == REASONING
            assert recorder.events == [shown_tool("lookup"), SHOWN_REASONING]
        finally:
            for gate in release.values():
                gate.set()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
