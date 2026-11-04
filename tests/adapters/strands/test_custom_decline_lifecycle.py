"""Custom stopping remains local to one room run and preserves cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool

from band.adapters.strands import StrandsAdapter
from band.core.types import MessageType
from band.testing import (
    FakeAgentTools,
    TextTurn,
    ToolTurn,
    events_of_type,
    failure_reports,
)
from tests.adapters.strands.declinepeer import DeclineModel, results
from tests.adapters.strands.kit import SEND_TURN, _run_message, run_judged_turn


class TestCustomDeclineLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    async def test_another_room_continues_while_declined_batch_is_held(
        self,
        kind: str,
        custom_decline_tools: Callable[..., Any],
        running_strands_turn: Callable[..., Any],
    ) -> None:
        release = asyncio.Event()

        async def decline(note: str) -> str:
            return note

        @strands_tool
        async def work(note: str) -> str:
            """Hold sibling work while another room runs."""
            await release.wait()
            return note

        model = DeclineModel(
            [
                (
                    ToolTurn("decline", {"note": "declined"}),
                    ToolTurn("work", {"note": "worked"}),
                ),
                SEND_TURN,
                TextTurn(""),
            ]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind], work]
        )
        await adapter.on_started("Bot", "A bot")
        first, second = (
            FakeAgentTools(room_id="room-1"),
            FakeAgentTools(room_id="room-2"),
        )

        async with running_strands_turn(run_judged_turn(adapter, first)) as task:
            await first.until(
                lambda: len(events_of_type(first, MessageType.TOOL_RESULT)) == 1
            )
            await _run_message(adapter, second, room_id="room-2")
            second.assert_message_sent(content="hi", count=1)
            assert not task.done()
            release.set()
            await task

        assert len(model.requests) == 3
        assert [
            result["toolUseId"]
            for result in results(adapter._message_history["room-1"])
        ] == ["call-decline", "call-work"]
        assert [
            result["toolUseId"]
            for result in results(adapter._message_history["room-2"])
        ] == ["call-band_send_message"]
        assert failure_reports(first) == failure_reports(second) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    async def test_cancelled_handler_is_not_a_success_and_next_turn_runs(
        self,
        kind: str,
        custom_decline_tools: Callable[..., Any],
        running_strands_turn: Callable[..., Any],
    ) -> None:
        entered, exited = asyncio.Event(), asyncio.Event()
        held = asyncio.Event()

        async def decline(note: str) -> str:
            entered.set()
            try:
                await held.wait()
                return note
            finally:
                exited.set()

        model = DeclineModel(
            [ToolTurn("decline", {"note": "held"}), SEND_TURN, TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        first, second = FakeAgentTools(), FakeAgentTools()

        async with running_strands_turn(_run_message(adapter, first)) as task:
            await entered.wait()
            task.cancel("cancel held decline")
            with pytest.raises(asyncio.CancelledError, match="cancel held decline"):
                await task

        assert exited.is_set()
        assert not first.turn.replied
        assert not first.turn.complete
        assert events_of_type(first, MessageType.TOOL_RESULT) == []
        await _run_message(adapter, second, is_session_bootstrap=False)
        second.assert_message_sent(content="hi", count=1)
        assert len(model.requests) == 3
        assert failure_reports(first) == failure_reports(second) == []
