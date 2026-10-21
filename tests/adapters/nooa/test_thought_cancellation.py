"""Turn cancellation releases thoughts even when a tool must report cancellation."""

from __future__ import annotations

import asyncio

import pytest
from nooa import Agent
from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaAdapter
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing.nooa import code_turn, result_turn, room_agent_class, room_call
from tests.adapters.nooa.kit import (
    Gate,
    SlowInput,
    TimelineTools,
    carrying,
    child_work,
    reasoned_result_turn,
    run_turn,
    running_task,
    thoughts,
    tool_outcomes,
)

HELD_THOUGHT = "Still working."
PENDING_TOOL = "pending-room-tool"
CANCELLED_SLOW = (
    "slow",
    "slow was cancelled because its turn ended first",
    True,
)


class TestThoughtCancellation:
    async def test_cancelling_one_caller_stops_its_tool_before_the_turn_ends(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought(HELD_THOUGHT)
        gate = Gate()
        llm = FakeLLMClient(
            [
                code_turn(
                    "pending = asyncio.create_task(self.room.slow(arguments={}))\n"
                    "await self.gate.entered.wait()"
                ),
                code_turn(
                    "pending.cancel()\n"
                    "try:\n"
                    "    await pending\n"
                    "except asyncio.CancelledError:\n"
                    "    pass\n"
                    "await self.gate.opened.wait()\n"
                    f"{room_call(BandTool.NO_REPLY)}",
                    reasoning=HELD_THOUGHT,
                ),
                result_turn(),
            ]
        )
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace=carrying("gate", gate)),
            additional_tools=[(SlowInput, gate.handler)],
            emit={Emit.THOUGHTS, Emit.TOOL_CALLS},
        )
        await adapter.on_started("Agent", "A test agent")
        turn = asyncio.create_task(run_turn(adapter, tools))
        try:
            await held.reached.wait()
            async with asyncio.timeout(5):
                await gate.cancellation_started.wait()

            assert not turn.done()
            assert gate.cancelled
            assert not gate.committed

            held.release.set()
            gate.opened.set()
            await turn
            assert tool_outcomes(tools)[0] == CANCELLED_SLOW
        finally:
            held.release.set()
            gate.opened.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)

    @pytest.mark.parametrize("cancel_at", ["body", "cleanup", None])
    async def test_cleanup_owns_the_tool_and_its_pending_thought(
        self, cancel_at: str | None
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought(HELD_THOUGHT)
        gate = Gate()
        after_thought = (
            "await asyncio.Event().wait()" if cancel_at == "body" else "pass"
        )
        llm = FakeLLMClient(
            [
                code_turn(
                    "asyncio.create_task(self.room.slow(arguments={}), "
                    f"name={PENDING_TOOL!r})\n"
                    "await self.gate.entered.wait()\n"
                    f"{room_call(BandTool.NO_REPLY)}"
                ),
                code_turn(after_thought, reasoning=HELD_THOUGHT),
                result_turn(),
            ]
        )
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace=carrying("gate", gate)),
            additional_tools=[(SlowInput, gate.handler)],
            emit={Emit.THOUGHTS, Emit.TOOL_CALLS},
        )
        await adapter.on_started("Agent", "A test agent")
        turn = asyncio.create_task(run_turn(adapter, tools))
        pending = None
        try:
            await held.reached.wait()
            pending = running_task(PENDING_TOOL)
            if cancel_at != "body":
                await gate.cancellation_started.wait()
            if cancel_at is None:
                assert not turn.done()
                held.release.set()
                await turn
                assert thoughts(tools) == [HELD_THOUGHT]
            else:
                turn.cancel()
                # Only a hung-test guard: the scenario is driven by the gates.
                async with asyncio.timeout(5):
                    with pytest.raises(asyncio.CancelledError):
                        await turn
                assert held.cancelled
                assert thoughts(tools) == []

            assert tool_outcomes(tools)[-1] == CANCELLED_SLOW
            assert gate.cancelled
            assert not gate.committed
        finally:
            held.release.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)

    async def test_cancellation_of_an_awaited_tool_reaches_turn_cleanup(self) -> None:
        tools = TimelineTools()
        held = tools.hold_thought(HELD_THOUGHT)
        gate = Gate()
        child_class = room_agent_class(
            FakeLLMClient(
                [
                    code_turn("await self.gate.entered.wait()"),
                    reasoned_result_turn(HELD_THOUGHT),
                ]
            ),
            base=Agent,
            name="Child",
            namespace=carrying("gate", gate) | {"work": child_work},
        )
        llm = FakeLLMClient(
            [
                code_turn(
                    "self.child_task = asyncio.create_task(self.worker.work())\n"
                    "await self.room.slow(arguments={})"
                ),
                result_turn(),
            ]
        )
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace=carrying("worker", child_class())),
            additional_tools=[(SlowInput, gate.handler)],
            emit={Emit.THOUGHTS, Emit.TOOL_CALLS},
        )
        await adapter.on_started("Agent", "A test agent")
        turn = asyncio.create_task(run_turn(adapter, tools))
        try:
            await held.reached.wait()
            turn.cancel()
            async with asyncio.timeout(5):
                with pytest.raises(asyncio.CancelledError):
                    await turn

            assert held.cancelled
            assert gate.cancelled
            assert thoughts(tools) == []
            assert tools.timeline == ["tool_call slow", "tool_result slow"]
        finally:
            held.release.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
