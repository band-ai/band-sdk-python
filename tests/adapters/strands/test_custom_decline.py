"""Successful custom declines stop the real Strands model loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from strands import tool as strands_tool

from band.adapters.strands import StrandsAdapter
from band.core.types import Emit
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import FakeAgentTools, TextTurn, ToolTurn, failure_reports
from tests.adapters.strands.declinepeer import DeclineModel, NativeStatus, results
from tests.adapters.strands.kit import SEND_TURN, _run_message, run_judged_turn


class TestCustomDecline:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize("emit", [(), (Emit.TOOL_CALLS,)])
    async def test_success_stops_before_another_request(
        self,
        kind: str,
        emit: tuple[Emit, ...],
        custom_decline_tools: Callable[..., Any],
    ) -> None:
        calls: list[str] = []

        async def decline(note: str) -> str:
            calls.append(note)
            return note

        model = DeclineModel([ToolTurn("decline", {"note": "declined"}), TextTurn("")])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind]], emit=emit
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        assert len(model.requests) == 1
        assert calls == ["declined"]
        assert tools.turn.replied
        assert failure_reports(tools) == []

        await _run_message(adapter, FakeAgentTools(), is_session_bootstrap=False)

        assert len(model.requests) == 2
        assert results(model.requests[1]) == [
            {
                "toolUseId": "call-decline",
                "status": NativeStatus.SUCCESS,
                "content": [{"text": "declined"}],
            }
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize(
        "effect", [TurnEffect.OBSERVE, TurnEffect.ACT, TurnEffect.REPLY]
    )
    async def test_other_effects_allow_followup_work(
        self, kind: str, effect: TurnEffect, custom_decline_tools: Callable[..., Any]
    ) -> None:
        async def work(note: str) -> str:
            return note

        model = DeclineModel(
            [ToolTurn("decline", {"note": "worked"}), SEND_TURN, TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(work, effect)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        assert len(model.requests) == 3
        tools.assert_message_sent(content="hi", count=1)
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    async def test_next_turn_uses_fresh_stop_state(
        self, kind: str, custom_decline_tools: Callable[..., Any]
    ) -> None:
        calls: list[str] = []

        async def decline(note: str) -> str:
            calls.append(note)
            return note

        model = DeclineModel(
            [ToolTurn("decline", {"note": "declined"}), SEND_TURN, TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        first, second = FakeAgentTools(), FakeAgentTools()

        await run_judged_turn(adapter, first)
        await _run_message(adapter, second, is_session_bootstrap=False)

        assert len(model.requests) == 3
        assert calls == ["declined"]
        second.assert_message_sent(content="hi", count=1)
        assert first.turn.replied and second.turn.replied
        assert failure_reports(first) == failure_reports(second) == []

    @pytest.mark.asyncio
    async def test_declared_native_alias_uses_its_own_effect(self) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        @strands_tool(name="band-band_no_reply")
        async def decline(note: str) -> str:
            """Decline using an exact registered custom alias."""
            return note

        model = DeclineModel([ToolTurn(decline.tool_name, {"note": "declined"})])
        adapter = StrandsAdapter(llm=model, additional_tools=[decline])
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        assert len(model.requests) == 1
        assert tools.turn.replied
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    async def test_legacy_terminal_marker_allows_followup_work(self) -> None:
        @strands_tool
        async def finish(note: str) -> str:
            """Perform work without declining."""
            return note

        finish.band_terminal = True  # type: ignore[attr-defined]
        model = DeclineModel(
            [ToolTurn("finish", {"note": "worked"}), SEND_TURN, TextTurn("")]
        )
        adapter = StrandsAdapter(llm=model, additional_tools=[finish])
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        assert len(model.requests) == 3
        tools.assert_message_sent(content="hi", count=1)
