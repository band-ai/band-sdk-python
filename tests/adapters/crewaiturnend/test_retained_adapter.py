"""Custom decline stops only its current retained-adapter turn."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.adapters.crewaiturnend.kit import DeclineInput, WorkInput, scripted_model
from tests.framework_conformance.turnprobes import (
    ToolCall,
    turn_input,
    turn_tools,
    user_message,
)


class TestRetainedAdapter:
    @pytest.mark.parametrize("kind", ["crewai", "flow"])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_the_next_turn_executes_new_work(
        self, kind: str, native: bool, retained_adapter: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            return "declined"

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> Any:
            calls.append(args.value)
            return "worked"

        model = scripted_model(native, ToolCall("decline", {"reason": "first"}))
        # Each independent turn gets its own ledger and unique input identity.
        async with retained_adapter(
            kind, model, [(DeclineInput, decline), (WorkInput, work)]
        ) as adapter:
            await adapter.on_event(turn_input(turn_tools()))
            model.responses.clear()
            followup = scripted_model(native, ToolCall("work", {"value": "second"}))
            model.responses.extend(followup.responses)
            second = turn_input(turn_tools(), replace(user_message(), id="second"))
            await adapter.on_event(second)

        assert model.asked == 3
        assert calls == ["first", "second"]

    @pytest.mark.parametrize("kind", ["crewai", "flow"])
    async def test_a_declining_room_does_not_stop_another_room(
        self, kind: str, retained_adapter: Any
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            return "declined"

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> Any:
            entered.set()
            await release.wait()
            calls.append(args.value)
            return "worked"

        # A script is shared only at the model boundary; dispatch is ordered by a gate.
        model = scripted_model(
            True,
            ToolCall("work", {"value": "other"}),
            ToolCall("decline", {"reason": "first"}),
        )
        async with retained_adapter(
            kind, model, [(DeclineInput, decline), (WorkInput, work)]
        ) as adapter:
            other = turn_input(
                turn_tools(), replace(user_message(), id="other", room_id="other-room")
            )
            other_task = asyncio.create_task(adapter.on_event(other))
            try:
                await entered.wait()
                await adapter.on_event(turn_input(turn_tools()))
                assert calls == ["first"]
                release.set()
                await other_task
            finally:
                release.set()
                if not other_task.done():
                    other_task.cancel()
                await asyncio.gather(other_task, return_exceptions=True)

        assert calls == ["first", "other"]
        assert model.asked == 3
