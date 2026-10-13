"""Backend-stop probes for the copilot_sdk adapter; see ``stopprobes``.

The adapter stops a room's backend turn with ``session.abort()``, which keeps
the session usable, so each probe reports what the room's sessions saw.
"""

from __future__ import annotations

import asyncio

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    make_started_adapter,
)
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_idle_room,
    wait_until,
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


async def end_running_turn(mode: ControlMode) -> list[str]:
    adapter, client, hang = await started()
    await stop_running_turn(adapter, hang, mode)
    return journal(client)


async def next_message_after_stop() -> list[str]:
    """The next message waits for the abort, then runs on the same session."""
    adapter, client, hang = await started()
    await stop_running_turn(adapter, hang, ControlMode.STOP)
    await adapter.on_event(turn_input(FakeAgentTools()))
    return journal(client)


async def idle() -> list[str]:
    adapter, client, _ = await started()
    await stop_idle_room(adapter, ROOM_ID)
    return journal(client)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=["session 1 prompt", "session 1 abort"],
        )
        for mode in ENDING_CONTROLS
    },
    "next-message-reuses-the-session": StopCase(
        run=next_message_after_stop,
        expected=["session 1 prompt", "session 1 abort", "session 1 prompt"],
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
