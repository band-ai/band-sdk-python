"""Reference history for ACP running-state sequences across session resets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import cast

from acp.helpers import update_tool_call
from acp.schema import ToolCallStatus
from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.core.protocols import AgentToolsProtocol
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.types import ToolStatus
from band.runtime.activity import (
    REASONING,
    UNKNOWN_ACTIVITY_TOOL_NAME,
    tool_call_activity,
)
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from tests.integrations.acp.clientbehavior.kit import band_activity_call, observed_tools

TOOLS = (BandTool.GET_PARTICIPANTS, BandTool.LOOKUP_PEERS)


class Step(StrEnum):
    START = auto()
    UPDATE = auto()
    RESET = auto()


@dataclass(frozen=True)
class Frame:
    step: Step
    slot: int
    status: ToolStatus | None

    @property
    def effective_status(self) -> ToolStatus | None:
        if self.step == Step.START and self.status is None:
            return ToolStatus.PENDING
        return self.status


def expected_activity(history: list[Frame]) -> AgentActivityDetail:
    """Select the most recently begun uninterrupted run in the wire history."""
    running: list[tuple[int, str]] = []
    for slot, tool in enumerate(TOOLS):
        frames = [
            (index, frame) for index, frame in enumerate(history) if frame.slot == slot
        ]
        terminal = next(
            (
                index
                for index, frame in frames
                if frame.effective_status in (ToolStatus.COMPLETED, ToolStatus.FAILED)
            ),
            None,
        )
        if terminal is not None:
            continue
        statuses = [
            (index, frame.effective_status)
            for index, frame in frames
            if frame.effective_status is not None
        ]
        if not statuses or statuses[-1][1] != ToolStatus.IN_PROGRESS:
            continue
        last_pending = max(
            (index for index, status in statuses if status == ToolStatus.PENDING),
            default=-1,
        )
        started = next(
            index
            for index, status in statuses
            if index > last_pending and status == ToolStatus.IN_PROGRESS
        )
        name = (
            tool
            if any(frame.step == Step.START for _, frame in frames)
            else UNKNOWN_ACTIVITY_TOOL_NAME
        )
        running.append((started, name))
    return tool_call_activity(max(running)[1]) if running else REASONING


async def play_steps(steps: list[tuple[Step, int, int, ToolStatus | None]]) -> None:
    """Drive real ACP collection and room activity against the history oracle."""
    client = ACPCollectingClient()
    recorders = [ActivityRecorder(), ActivityRecorder()]
    histories: list[list[Frame]] = [[], []]
    retired: list[tuple[ActivityRecorder, list[dict[str, object]]]] = []

    def attach(room: int) -> None:
        client.set_sink(
            str(room),
            RoomTurnEmitter(
                cast(AgentToolsProtocol, observed_tools(recorders[room])),
                emit=frozenset(),
                activity_name=lambda call: call.mcp_tool or UNKNOWN_ACTIVITY_TOOL_NAME,
            ),
        )

    for room in range(2):
        attach(room)
    for step, room, slot, status in steps:
        if step == Step.RESET:
            retired.append((recorders[room], list(recorders[room].events)))
            client.reset_session(str(room))
            recorders[room] = ActivityRecorder()
            histories[room] = []
            attach(room)
        else:
            frame = Frame(step, slot, status)
            histories[room].append(frame)
            update = (
                band_activity_call(str(slot), TOOLS[slot], status)
                if step == Step.START
                else update_tool_call(
                    str(slot), status=cast(ToolCallStatus | None, status)
                )
            )
            await client.session_update(str(room), update)
        for current in range(2):
            assert recorders[current].activity.detail == expected_activity(
                histories[current]
            )
        for recorder, events in retired:
            assert recorder.events == events
