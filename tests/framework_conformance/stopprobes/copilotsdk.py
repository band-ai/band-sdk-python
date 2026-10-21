"""Backend-stop probes for the copilot_sdk adapter; see ``stopprobes``.

The adapter stops a room's backend turn with ``session.abort()``, which keeps
the session usable, so each probe reports what the room's sessions saw.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.client.streaming import ControlMode
from band.runtime.tools import BandTool
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    make_started_adapter,
)
from tests.adapters.waiting import spin
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    cancel_turn,
    end_turn,
    stop_room,
    wait_until,
    waiting_on_stop,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class FirstTurnHangs:
    """A scripted mid-turn step: the first turn never finishes, later ones do."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def __call__(self, session: FakeCopilotSession) -> None:
        if self.entered.is_set():
            return
        self.entered.set()
        await asyncio.Future[None]()


def journal(client: FakeCopilotClient) -> list[str]:
    """Every session's prompts, aborts and disconnects, by session number."""
    return [
        f"session {number} {entry}"
        for number, session in enumerate(client.sessions, start=1)
        for entry in session.journal
    ]


async def started() -> tuple[CopilotSDKAdapter, FakeCopilotClient, FirstTurnHangs]:
    hang = FirstTurnHangs()
    client = FakeCopilotClient(turn_events=[hang])
    return await make_started_adapter(client), client, hang


async def stop_running_turn(
    adapter: CopilotSDKAdapter, hang: FirstTurnHangs, mode: ControlMode
) -> None:
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(hang.entered.is_set)
    await end_turn(adapter, turn, ROOM_ID, mode)


def hold_abort(session: FakeCopilotSession) -> None:
    session.abort_released.clear()


def hold_aborted_idle(session: FakeCopilotSession) -> None:
    session.holds_aborted_idle = True


async def stop_holding(
    adapter: CopilotSDKAdapter,
    client: FakeCopilotClient,
    hang: FirstTurnHangs,
    tools: FakeAgentTools,
    hold: Callable[[FakeCopilotSession], None],
) -> FakeCopilotSession:
    """Stop the running turn and leave part of its abort in flight, as ``hold`` says."""
    turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
    await wait_until(hang.entered.is_set)
    session = client.sessions[0]
    hold(session)
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    return session


async def end_running_turn(mode: ControlMode) -> list[str]:
    adapter, client, hang = await started()
    await stop_running_turn(adapter, hang, mode)
    return journal(client)


async def next_message_waits_for_the_abort() -> list[str]:
    """The next message waits for the abort, then runs on the same session."""
    adapter, client, hang = await started()
    session = await stop_holding(adapter, client, hang, FakeAgentTools(), hold_abort)
    next_turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    # The turn blocks inside the context manager that owns the wait, where
    # ``waiting_on_stop`` cannot see it; a turn that ignored the abort would
    # prompt the session within these loop turns.
    await spin()
    session.abort_released.set()
    await next_turn
    return journal(client)


async def next_message_waits_for_the_aborted_idle() -> list[str]:
    """The next message waits for the idle that ends the aborted turn: arriving
    after the abort is acknowledged, it would end that message's prompt with no
    reply."""
    adapter, client, hang = await started()
    session = await stop_holding(
        adapter, client, hang, FakeAgentTools(), hold_aborted_idle
    )
    next_turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    # As in ``next_message_waits_for_the_abort``, the wait is out of
    # ``waiting_on_stop``'s sight: a turn that ignored the idle would prompt the
    # session within these loop turns.
    await spin()
    session.deliver_aborted_idle()
    await next_turn
    return journal(client)


async def late_tool_call_during_held_abort() -> list[str]:
    """A tool call from the stopped turn that lands while its abort is pending
    is answered on that turn's tools, not on the replacement turn's."""
    from copilot import (  # noqa: PLC0415 -- copilot extra, absent from some lane venvs
        ToolInvocation,
    )

    adapter, client, hang = await started()
    stopped, replacement = ToolSchemaFakeTools(), ToolSchemaFakeTools()
    session = await stop_holding(adapter, client, hang, stopped, hold_abort)
    next_turn = asyncio.create_task(adapter.on_event(turn_input(replacement)))
    await spin()
    await session.find_tool(BandTool.SEND_MESSAGE).handler(
        ToolInvocation(
            tool_call_id="late-call",
            tool_name=BandTool.SEND_MESSAGE,
            arguments={"content": "from the stopped turn", "mentions": []},
        )
    )
    session.abort_released.set()
    await next_turn
    return [
        *(
            f"stopped turn's tools got {call['tool_name']}"
            for call in stopped.tool_calls
        ),
        *(
            f"replacement turn's tools got {call['tool_name']}"
            for call in replacement.tool_calls
        ),
    ]


async def cleanup_waits_for_the_abort() -> list[str]:
    """Leaving the room waits for the abort before it disconnects the session."""
    adapter, client, hang = await started()
    session = await stop_holding(adapter, client, hang, FakeAgentTools(), hold_abort)
    cleanup = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
    await wait_until(lambda: waiting_on_stop(cleanup) or cleanup.done())
    session.abort_released.set()
    await cleanup
    return journal(client)


async def shutdown_right_after_stop() -> list[str]:
    """Shutdown waits for the room's abort before it disconnects the session."""
    adapter, client, hang = await started()
    session = await stop_holding(adapter, client, hang, FakeAgentTools(), hold_abort)
    shutdown = asyncio.create_task(adapter.cleanup_all())
    await wait_until(lambda: waiting_on_stop(shutdown) or shutdown.done())
    session.abort_released.set()
    await shutdown
    return journal(client)


async def idle() -> list[str]:
    adapter, client, _ = await started()
    await stop_room(adapter, ROOM_ID)
    return journal(client)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=["session 1 prompt", "session 1 abort", "session 1 idle"],
        )
        for mode in ENDING_CONTROLS
    },
    "next-message-waits-for-the-abort": StopCase(
        run=next_message_waits_for_the_abort,
        expected=[
            "session 1 prompt",
            "session 1 abort",
            "session 1 idle",
            "session 1 prompt",
        ],
    ),
    "next-message-waits-for-the-aborted-idle": StopCase(
        run=next_message_waits_for_the_aborted_idle,
        expected=[
            "session 1 prompt",
            "session 1 abort",
            "session 1 idle",
            "session 1 prompt",
        ],
    ),
    "late-tool-call-during-held-abort": StopCase(
        run=late_tool_call_during_held_abort,
        expected=[f"stopped turn's tools got {BandTool.SEND_MESSAGE}"],
    ),
    "cleanup-waits-for-the-abort": StopCase(
        run=cleanup_waits_for_the_abort,
        expected=[
            "session 1 prompt",
            "session 1 abort",
            "session 1 idle",
            "session 1 disconnect",
        ],
    ),
    "shutdown-right-after-stop": StopCase(
        run=shutdown_right_after_stop,
        expected=[
            "session 1 prompt",
            "session 1 abort",
            "session 1 idle",
            "session 1 disconnect",
        ],
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
