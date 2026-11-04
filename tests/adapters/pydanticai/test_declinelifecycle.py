"""A custom decline stops only its own Pydantic run and preserves cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import BandTool, TurnEffect
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.pydanticai.declinepeer import (
    CUSTOM,
    REASON,
    SUCCESS,
    CustomDeclineInput,
    ScriptedModel,
    native_registration,
    portable_registration,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, user_message


class TestDeclineLifecycle:
    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_declining_room_leaves_gated_room_running(
        self, register: Callable[..., Any]
    ) -> None:
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            entered.set()
            try:
                await release.wait()
                return SUCCESS
            finally:
                finished.set()

        model = ScriptedModel([(CUSTOM, {"reason": REASON})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        slow, fast = FakeAgentTools(room_id="slow"), FakeAgentTools(room_id="fast")
        async with asyncio.TaskGroup() as group:
            task = group.create_task(
                adapter.on_event(
                    turn_input(slow, replace(user_message(), room_id="slow"))
                )
            )
            try:
                await entered.wait()
                await adapter.on_event(
                    turn_input(fast, replace(user_message(), room_id="fast"))
                )
                assert not task.done()
                assert not finished.is_set()
            finally:
                release.set()
        assert len(model.requests) == 2
        assert slow.turn.replied and fast.turn.replied
        assert reported_failures(slow) == reported_failures(fast) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_cancellation_preserves_control_and_cleanup(
        self, register: Callable[..., Any]
    ) -> None:
        entered, finished = asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
                return SUCCESS
            finally:
                finished.set()

        model = ScriptedModel([(CUSTOM, {"reason": REASON})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        task = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await entered.wait()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(model.requests) == 1
        assert finished.is_set()
        assert not tools.turn.replied
        assert reported_failures(tools) == []
