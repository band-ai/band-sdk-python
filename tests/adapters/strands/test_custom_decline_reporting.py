"""Reporting failures never change custom decline execution classification."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from band.adapters.strands import StrandsAdapter
from band.core.types import Emit
from band.testing import FakeAgentTools, TextTurn, ToolTurn
from tests.adapters.strands.declinepeer import DeclineModel, results
from tests.adapters.strands.kit import _run_message


class TestCustomDeclineReporting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize("emit", [(), (Emit.TOOL_CALLS,)])
    @pytest.mark.parametrize(
        ("output", "status", "replied", "requests"),
        [
            ("declined", "success", True, 1),
            ({"ok": False}, "error", False, 2),
        ],
    )
    async def test_failed_event_transport_preserves_status_and_stopping(
        self,
        kind: str,
        emit: tuple[Emit, ...],
        output: Any,
        status: str,
        replied: bool,
        requests: int,
        custom_decline_tools: Callable[..., Any],
    ) -> None:
        async def decline(note: str) -> Any:
            return output

        model = DeclineModel([ToolTurn("decline", {"note": "go"}), TextTurn("")])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind]], emit=emit
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("event transport unavailable")

        await _run_message(adapter, tools)

        assert len(model.requests) == requests
        assert results(adapter._message_history["room-1"])[0]["status"] == status
        assert tools.turn.replied is replied
        assert tools.turn.complete is replied
