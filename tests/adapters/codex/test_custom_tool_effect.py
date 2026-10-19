"""Codex adapter: custom tool effect."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.adapters.codex.kit import (
    StayQuietInput,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    final_text,
    tool_call_request,
    turn_completed,
)
from tests.framework_conformance.turnprobes import undeclared


class TestCustomToolEffect:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("declare", "chat"),
        [
            pytest.param(
                declares_turn_effect(TurnEffect.DECLINE), [], id="declared-silence"
            ),
            pytest.param(undeclared, [], id="undeclared"),
        ],
    )
    async def test_only_a_declared_tool_settles_the_reply(
        self, declare: Callable[..., Any], chat: list[str]
    ) -> None:
        async def stay_quiet(args: StayQuietInput) -> str:
            return "quiet"

        turn = await run_codex_turn(
            events=[
                tool_call_request(1, "stayquiet"),
                final_text("Nothing to add."),
                turn_completed(),
            ],
            additional_tools=[(StayQuietInput, declare(stay_quiet))],
        )

        assert turn.tools.chat == chat

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("declare", "complete"),
        [
            pytest.param(declares_turn_effect(TurnEffect.ACT), True, id="declared"),
            pytest.param(undeclared, False, id="undeclared-observes"),
        ],
    )
    async def test_the_tool_records_its_effect_on_the_turn(
        self, declare: Callable[..., Any], complete: bool
    ) -> None:
        async def stay_quiet(args: StayQuietInput) -> str:
            return "quiet"

        turn = await run_codex_turn(
            events=[tool_call_request(1, "stayquiet"), turn_completed()],
            additional_tools=[(StayQuietInput, declare(stay_quiet))],
        )

        assert turn.tools.turn.complete is complete
