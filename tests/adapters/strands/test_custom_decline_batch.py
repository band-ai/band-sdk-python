"""Strands settles every concurrent sibling before honoring a custom decline."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool
from strands.types.tools import ToolResult

from band.adapters.strands import StrandsAdapter
from band.core.types import MessageType
from band.testing import (
    FakeAgentTools,
    TextTurn,
    ToolTurn,
    events_of_type,
    failure_reports,
)
from tests.adapters.strands.declinepeer import DeclineModel, NativeStatus, results
from tests.adapters.strands.kit import _run_message, run_judged_turn


class TestCustomDeclineBatch:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize(
        ("calls", "sibling_output", "expected"),
        [
            pytest.param(
                (
                    ToolTurn("decline", {"note": "declined"}),
                    ToolTurn("work", {"note": "worked"}),
                ),
                {"ok": True},
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": "declined"}],
                    },
                    {
                        "toolUseId": "call-work",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": '{"ok": true}'}],
                    },
                ],
                id="decline-first-success",
            ),
            pytest.param(
                (
                    ToolTurn("decline", {"note": "declined"}),
                    ToolTurn("work", {"note": "worked"}),
                ),
                {"ok": False},
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": "declined"}],
                    },
                    {
                        "toolUseId": "call-work",
                        "status": NativeStatus.ERROR,
                        "content": [{"text": '{"ok": false}'}],
                    },
                ],
                id="decline-first-failure",
            ),
            pytest.param(
                (
                    ToolTurn("work", {"note": "worked"}),
                    ToolTurn("decline", {"note": "declined"}),
                ),
                {"ok": True},
                [
                    {
                        "toolUseId": "call-work",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": '{"ok": true}'}],
                    },
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": "declined"}],
                    },
                ],
                id="work-first-success",
            ),
            pytest.param(
                (
                    ToolTurn("work", {"note": "worked"}),
                    ToolTurn("decline", {"note": "declined"}),
                ),
                {"ok": False},
                [
                    {
                        "toolUseId": "call-work",
                        "status": NativeStatus.ERROR,
                        "content": [{"text": '{"ok": false}'}],
                    },
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.SUCCESS,
                        "content": [{"text": "declined"}],
                    },
                ],
                id="work-first-failure",
            ),
        ],
    )
    async def test_held_sibling_settles_and_all_results_survive(
        self,
        kind: str,
        calls: tuple[ToolTurn, ...],
        sibling_output: dict[str, bool],
        expected: list[ToolResult],
        custom_decline_tools: Callable[..., Any],
        running_strands_turn: Callable[..., Any],
    ) -> None:
        entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        completed: list[str] = []

        async def decline(note: str) -> str:
            return note

        @strands_tool
        async def work(note: str) -> dict[str, bool]:
            """Complete sibling work before the batch stops."""
            entered.set()
            try:
                await release.wait()
                completed.append(note)
                return sibling_output
            finally:
                exited.set()

        model = DeclineModel([calls, TextTurn("")])
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

        assert completed == ["worked"]
        assert exited.is_set()
        assert len(model.requests) == 1
        assert tools.turn.replied
        assert failure_reports(tools) == []

        await _run_message(adapter, FakeAgentTools(), is_session_bootstrap=False)

        assert len(model.requests) == 2
        assert results(model.requests[1]) == expected
