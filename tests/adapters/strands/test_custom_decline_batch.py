"""Strands settles every concurrent sibling before honoring a custom decline."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool

from band.adapters.strands import StrandsAdapter
from band.core.types import MessageType
from band.testing import FakeAgentTools, ToolTurn, events_of_type, failure_reports
from tests.adapters.strands.declinepeer import DeclineModel, results
from tests.adapters.strands.kit import run_judged_turn


class TestCustomDeclineBatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize(
        "reverse", [False, True], ids=["decline-first", "work-first"]
    )
    @pytest.mark.parametrize(
        "failed_sibling", [False, True], ids=["work-success", "work-failure"]
    )
    async def test_held_sibling_settles_and_all_results_survive(
        self,
        kind: str,
        reverse: bool,
        failed_sibling: bool,
        custom_decline_tools: Callable[..., Any],
        running_strands_turn: Callable[..., Any],
    ) -> None:
        entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        completed: list[str] = []

        async def decline(note: str) -> str:
            return note

        @strands_tool
        async def work(note: str) -> Any:
            """Complete sibling work before the batch stops."""
            entered.set()
            try:
                await release.wait()
                completed.append(note)
                return {"ok": not failed_sibling}
            finally:
                exited.set()

        calls = (
            ToolTurn("decline", {"note": "declined"}),
            ToolTurn("work", {"note": "worked"}),
        )
        model = DeclineModel([tuple(reversed(calls)) if reverse else calls])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind], work]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        async with running_strands_turn(run_judged_turn(adapter, tools)) as task:
            await entered.wait()
            await tools.until(
                lambda: len(events_of_type(tools, MessageType.TOOL_RESULT)) == 1
            )
            assert not task.done()
            assert not exited.is_set()
            assert len(model.requests) == 1
            release.set()
            await task

        expected = [
            {
                "toolUseId": "call-decline",
                "status": "success",
                "content": [{"text": "declined"}],
            },
            {
                "toolUseId": "call-work",
                "status": "error" if failed_sibling else "success",
                "content": [
                    {"text": '{"ok": false}' if failed_sibling else '{"ok": true}'}
                ],
            },
        ]
        assert results(adapter._message_history["room-1"]) == (
            list(reversed(expected)) if reverse else expected
        )
        assert completed == ["worked"]
        assert exited.is_set()
        assert len(model.requests) == 1
        assert tools.turn.replied
        assert failure_reports(tools) == []
