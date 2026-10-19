"""Backend-stop probes for the codex adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from band.adapters.codex import CodexAdapter
from band.client.streaming import ControlMode
from band.integrations.codex import CodexRequestMethod
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.testing.fake_tools import FakeAgentTools
from band.testing.platform import platform_connection_stub
from tests.adapters.codexturns import FakeCodexClient, make_codex_adapter
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    STARTUP_TIMEOUT_SECONDS,
    StopCase,
    cancel_turn,
    end_turn,
    stop_room,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, user_message
from tests.runtime.runtimecontrol.kit import control_payload, never_executes

SLOW_ROOMS = ["room-slow-1", "room-slow-2"]


def interrupt_of(thread_id: str, turn_id: str) -> str:
    return f"turn/interrupt {thread_id} {turn_id}"


def still_stopping(room_id: str) -> str:
    return f"still stopping {room_id}"


# The fake app-server numbers its threads and turns from 1.
INTERRUPT_FIRST_TURN = interrupt_of("thr-1", "turn-1")


def interrupts(client: FakeCodexClient) -> list[str]:
    return [
        interrupt_of(params["threadId"], params["turnId"])
        for params in client.params_of(CodexRequestMethod.TURN_INTERRUPT)
    ]


async def started() -> tuple[CodexAdapter, FakeCodexClient]:
    client = FakeCodexClient(hold_when_drained=True)
    adapter = make_codex_adapter(client)
    await adapter.on_started("Agent", "A coding agent")
    return adapter, client


async def end_running_turn(mode: ControlMode) -> list[str]:
    adapter, client = await started()
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: bool(client.params_of(CodexRequestMethod.TURN_START)))
    await end_turn(adapter, turn, ROOM_ID, mode)
    return interrupts(client)


async def stop_while_turn_starts(mode: ControlMode) -> list[str]:
    """A stop that lands before turn/start answers still interrupts that turn."""
    adapter, client = await started()
    client.turn_start_released.clear()
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: bool(client.params_of(CodexRequestMethod.TURN_START)))
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, mode)
    client.turn_start_released.set()
    await adapter.backend_turns.drain()
    return interrupts(client)


async def next_turn_after_stop() -> list[str]:
    """The next turn's turn/start goes out only after the interrupt."""
    adapter, client = await started()
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: bool(client.params_of(CodexRequestMethod.TURN_START)))
    await end_turn(adapter, turn, ROOM_ID, ControlMode.STOP)
    nxt = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    try:
        await wait_until(
            lambda: len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        )
        return [
            method
            for method in client.request_methods
            if method
            in {CodexRequestMethod.TURN_START, CodexRequestMethod.TURN_INTERRUPT}
        ]
    finally:
        await end_turn(adapter, nxt, ROOM_ID, ControlMode.STOP)


async def agent_wide_stop_with_slow_backends() -> list[str]:
    """The control task returns while every room's interrupt is still in flight."""
    adapter, client = await started()
    client.interrupt_released.clear()
    platform = platform_connection_stub()
    link = BandLink(agent_id=platform.agent_id, api_key=platform.api_key)
    runtime = AgentRuntime(
        link=link,
        agent_id=platform.agent_id,
        on_execute=never_executes,
        on_control=adapter.on_interrupt,
    )
    turns = []
    for room_id in SLOW_ROOMS:
        runtime.executions[room_id] = ExecutionContext(
            room_id, link, never_executes, agent_id=platform.agent_id
        )
        message = replace(user_message(), room_id=room_id)
        turns.append(
            asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools(), message)))
        )
    await wait_until(
        lambda: len(client.params_of(CodexRequestMethod.TURN_START)) == len(SLOW_ROOMS)
    )
    for turn in turns:
        turn.cancel()
    await asyncio.gather(*turns, return_exceptions=True)

    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        await runtime.handle_control(control_payload(ControlMode.STOP))
    journal = [
        *interrupts(client),
        *(
            still_stopping(room_id)
            for room_id in SLOW_ROOMS
            if adapter.backend_turns.stopping(room_id)
        ),
    ]
    client.interrupt_released.set()
    await adapter.backend_turns.drain()
    return journal


async def idle() -> list[str]:
    adapter, client = await started()
    await stop_room(adapter, ROOM_ID)
    return interrupts(client)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=[INTERRUPT_FIRST_TURN],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-while-turn-starts": StopCase(
            run=lambda mode=mode: stop_while_turn_starts(mode),
            expected=[INTERRUPT_FIRST_TURN],
        )
        for mode in ENDING_CONTROLS
    },
    "next-turn-starts-after-the-interrupt": StopCase(
        run=next_turn_after_stop,
        expected=[
            CodexRequestMethod.TURN_START,
            CodexRequestMethod.TURN_INTERRUPT,
            CodexRequestMethod.TURN_START,
        ],
    ),
    "agent-wide-stop-returns-before-slow-backends": StopCase(
        run=agent_wide_stop_with_slow_backends,
        expected=[
            INTERRUPT_FIRST_TURN,
            interrupt_of("thr-2", "turn-2"),
            *map(still_stopping, SLOW_ROOMS),
        ],
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
