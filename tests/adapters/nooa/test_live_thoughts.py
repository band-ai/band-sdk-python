"""Each model call's thought reaches the room as soon as that call completes."""

from __future__ import annotations

import asyncio

import pytest
from nooa import GenerationError
from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaAdapter
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing.nooa import code_turn, custom_room_call, result_turn, room_call
from tests.adapters.nooa.kit import (
    CHILD_TASK,
    Gate,
    OrderInput,
    SlowInput,
    TimelineTools,
    bare_binding,
    delegating_room_agent,
    dispatched,
    failing_llm,
    find_order,
    reasoned_result_turn,
    run_turn,
    running_task,
    started_adapter,
    thoughts,
)

REPLY = {"content": "Hi Alice", "mentions": ["Alice"]}


class TestLiveThoughts:
    async def test_a_thought_posts_while_its_calls_code_is_still_running(
        self,
    ) -> None:
        tools = TimelineTools()
        gate = Gate()
        llm = FakeLLMClient(
            [
                code_turn(custom_room_call("slow"), reasoning="Look it up first."),
                code_turn(
                    room_call(BandTool.SEND_MESSAGE, REPLY), reasoning="Now answer."
                ),
                result_turn(),
            ]
        )
        adapter = await started_adapter(
            llm,
            additional_tools=[(SlowInput, gate.handler)],
            emit={Emit.TOOL_CALLS, Emit.THOUGHTS},
        )

        turn = asyncio.create_task(run_turn(adapter, tools))
        await gate.entered.wait()

        assert thoughts(tools) == ["Look it up first."]

        gate.opened.set()
        await turn

        assert tools.timeline == [
            "thought Look it up first.",
            "tool_call slow",
            "tool_result slow",
            "thought Now answer.",
            f"tool_call {BandTool.SEND_MESSAGE}",
            f"dispatch {BandTool.SEND_MESSAGE}",
            f"tool_result {BandTool.SEND_MESSAGE}",
        ]

    async def test_a_child_agents_thought_posts_before_the_turns_reply(self) -> None:
        tools = TimelineTools()
        agent = delegating_room_agent(
            code_turn("print('working')", reasoning="Delegated reasoning."),
            result_turn(),
        )
        adapter = NooaAdapter(agent=agent, emit={Emit.TOOL_CALLS, Emit.THOUGHTS})
        await adapter.on_started("Agent", "A test agent")

        await run_turn(adapter, tools)

        assert tools.timeline == [
            "thought Delegated reasoning.",
            f"tool_call {BandTool.NO_REPLY}",
            f"dispatch {BandTool.NO_REPLY}",
            f"tool_result {BandTool.NO_REPLY}",
        ]

    async def test_closing_the_binding_waits_for_a_thought_still_posting(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought("All done.")
        binding = bare_binding(tools)
        binding.post_thought("All done.")

        closing = asyncio.create_task(binding.close())
        await held.reached.wait()

        assert not closing.done()

        held.release.set()
        await closing

        assert thoughts(tools) == ["All done."]

    async def test_cancelling_the_turn_cancels_a_thought_still_posting(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought("All done.")
        llm = FakeLLMClient(
            [code_turn(room_call(BandTool.NO_REPLY)), reasoned_result_turn("All done.")]
        )
        adapter = await started_adapter(llm, emit={Emit.THOUGHTS})

        turn = asyncio.create_task(run_turn(adapter, tools))
        await held.reached.wait()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        assert held.cancelled
        assert thoughts(tools) == []

    async def test_cancelling_the_turn_cancels_a_child_whose_thought_is_held(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought("Waiting forever.")
        agent = delegating_room_agent(
            code_turn("await asyncio.Event().wait()", reasoning="Waiting forever."),
            result_turn(),
        )
        adapter = NooaAdapter(agent=agent, emit={Emit.THOUGHTS})
        await adapter.on_started("Agent", "A test agent")

        turn = asyncio.create_task(run_turn(adapter, tools))
        await held.reached.wait()
        child = running_task(CHILD_TASK)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        held.release.set()

        assert child.cancelled()
        assert held.cancelled
        assert thoughts(tools) == []

    async def test_a_closed_binding_drops_a_late_thought(self) -> None:
        tools = TimelineTools()
        binding = bare_binding(tools)
        await binding.close()

        binding.post_thought("Too late.")

        assert binding.thoughts == set()
        assert thoughts(tools) == []

    async def test_a_reply_cannot_overtake_a_thought_without_tool_events(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought("Say hi back.")
        llm = FakeLLMClient(
            [
                code_turn(
                    room_call(BandTool.SEND_MESSAGE, REPLY), reasoning="Say hi back."
                ),
                result_turn(),
            ]
        )
        adapter = await started_adapter(llm, emit={Emit.THOUGHTS})

        turn = asyncio.create_task(run_turn(adapter, tools))
        await held.reached.wait()

        assert dispatched(tools) == []

        held.release.set()
        await turn

        assert tools.timeline == [
            "thought Say hi back.",
            f"dispatch {BandTool.SEND_MESSAGE}",
        ]

    async def test_a_failing_turn_posts_its_thought_before_the_failure(
        self,
    ) -> None:
        tools = TimelineTools()
        llm = failing_llm(
            "provider down", code_turn("print('thinking')", reasoning="Halfway.")
        )
        adapter = await started_adapter(llm, emit={Emit.THOUGHTS})

        with pytest.raises(GenerationError, match="provider down"):
            await run_turn(adapter, tools)

        assert tools.timeline == ["thought Halfway.", "error"]

    async def test_a_thought_precedes_every_tool_call_its_cell_gathers(
        self,
    ) -> None:
        tools = TimelineTools()
        held = tools.hold_thought("Check both orders.")
        lookups = ", ".join(
            custom_room_call("order", {"order_id": order_id}).removeprefix("await ")
            for order_id in ("A-1", "A-2")
        )
        llm = FakeLLMClient(
            [
                code_turn(
                    f"await asyncio.gather({lookups})\n{room_call(BandTool.NO_REPLY)}",
                    reasoning="Check both orders.",
                ),
                result_turn(),
            ]
        )
        adapter = await started_adapter(
            llm,
            additional_tools=[(OrderInput, find_order)],
            emit={Emit.TOOL_CALLS, Emit.THOUGHTS},
        )

        turn = asyncio.create_task(run_turn(adapter, tools))
        await held.reached.wait()

        assert tools.timeline == []

        held.release.set()
        await turn

        assert tools.timeline == [
            "thought Check both orders.",
            "tool_call order",
            "tool_call order",
            "tool_result order",
            "tool_result order",
            f"tool_call {BandTool.NO_REPLY}",
            f"dispatch {BandTool.NO_REPLY}",
            f"tool_result {BandTool.NO_REPLY}",
        ]
