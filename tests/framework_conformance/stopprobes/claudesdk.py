"""Backend-stop probes for the claude_sdk adapter; see ``stopprobes``.

The adapter stops a room's backend turn by retiring the room's CLI client, so
each probe reports the scripted CLI processes that were closed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from band.client.streaming import ControlMode
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_idle_room,
    wait_until,
)

if TYPE_CHECKING:
    from tests.adapters.claude_sdk.fakecli import FakeClaude
    from tests.adapters.claude_sdk.helpers import ClaudeRoom

# The fake CLI numbers its first session; a retired client keeps its id.
CLOSE_FIRST_SESSION = "close sess-1"


def closes(claude: FakeClaude) -> list[str]:
    """Each closed CLI process, by the session it served."""
    return [
        f"close {session.session_id}"
        for session in claude.sessions
        if not session.alive
    ]


def live_processes(claude: FakeClaude) -> list[str]:
    return [
        f"live process {number}"
        for number, session in enumerate(claude.sessions, start=1)
        if session.alive
    ]


@asynccontextmanager
async def claude_room(config: Any = None) -> AsyncIterator[ClaudeRoom]:
    """A started adapter's room on the scripted CLI, torn down on exit."""
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        ClaudeSDKAdapter,
    )
    from tests.adapters.claude_sdk.fakecli import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        FakeClaude,
    )
    from tests.adapters.claude_sdk.helpers import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        ClaudeRoom,
    )

    claude = FakeClaude()
    adapter = ClaudeSDKAdapter(config)
    with patch(
        "band.integrations.claude_sdk.session_manager.create_transport",
        claude.transport,
    ):
        await adapter.on_started("Agent", "An agent under test")
        try:
            yield ClaudeRoom(adapter, claude)
        finally:
            await adapter.cleanup_all()


async def stop_running_turn(room: ClaudeRoom, mode: ControlMode) -> None:
    """Park a turn in the CLI, then end it as the runtime does."""
    from tests.adapters.claude_sdk.fakecli import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        Hold,
    )

    working = Hold()
    room.claude.script([working])
    turn = room.send_in_background("work")
    await wait_until(working.reached.is_set)
    await end_turn(room.adapter, turn, room.room_id, mode)


async def end_running_turn(mode: ControlMode) -> list[str]:
    async with claude_room() as room:
        await stop_running_turn(room, mode)
        return closes(room.claude)


async def next_turn_after_stop() -> list[str]:
    """The next message runs on a fresh CLI process, never the retired one."""
    async with claude_room() as room:
        await stop_running_turn(room, ControlMode.STOP)
        room.claude.script([room.model_reply("Done.")])
        await room.send("again")
        await room.settled()
        return [*closes(room.claude), *live_processes(room.claude)]


async def stop_after_approval_release() -> list[str]:
    """A turn released to wait on a manual approval is still stopped."""
    from tests.adapters.claude_sdk.helpers import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        with_approvals,
    )
    from tests.baseline.decisions import (  # noqa: PLC0415 -- paired with the claude_sdk fake CLI above
        ModelDecision,
    )

    async with claude_room(with_approvals()) as room:
        room.claude.script([ModelDecision.call("Bash", command="ls")])
        await room.send("List the files")
        await room.adapter.on_interrupt(room.room_id, ControlMode.STOP)
        await room.settled()
        await room.adapter.backend_turns.drain()
        return closes(room.claude)


async def idle() -> list[str]:
    async with claude_room() as room:
        await stop_idle_room(room.adapter, room.room_id)
        return closes(room.claude)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=[CLOSE_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "next-turn-gets-a-fresh-client": StopCase(
        run=next_turn_after_stop,
        expected=[CLOSE_FIRST_SESSION, "live process 2"],
    ),
    "stop-after-approval-release": StopCase(
        run=stop_after_approval_release, expected=[CLOSE_FIRST_SESSION]
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
