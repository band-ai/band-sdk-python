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
from tests.integrations.acp.clientbehavior.kit import (
    band_activity_call,
    band_activity_metadata,
    observed_tools,
)

TOOLS = (BandTool.GET_PARTICIPANTS, BandTool.LOOKUP_PEERS)


class Step(StrEnum):
    START = auto()
    UPDATE = auto()
    IDENTITY = auto()
    REJECT_IDENTITY = auto()
    RESET = auto()


@dataclass(frozen=True)
class Frame:
    step: Step
    slot: int
    status: ToolStatus | None

    @property
    def effective_status(self) -> ToolStatus | None:
        if self.step in (Step.IDENTITY, Step.REJECT_IDENTITY):
            return None
        if self.step == Step.START and self.status is None:
            return ToolStatus.PENDING
        return self.status


def expected_activity(history: list[Frame]) -> AgentActivityDetail:
    """Select the most recently begun uninterrupted run in the wire history."""
    activations: list[tuple[int, int, list[tuple[int, Frame]]]] = []
    for slot, _tool in enumerate(TOOLS):
        frames = [
            (index, frame) for index, frame in enumerate(history) if frame.slot == slot
        ]
        terminal = next(
            (
                index
                for index, frame in frames
                if frame.effective_status in (ToolStatus.COMPLETED, ToolStatus.FAILED)
            ),
            len(history),
        )
        statuses = [
            (index, frame.effective_status)
            for index, frame in frames
            if index <= terminal and frame.effective_status is not None
        ]
        starts = [
            index
            for offset, (index, status) in enumerate(statuses)
            if status == ToolStatus.IN_PROGRESS
            and (offset == 0 or statuses[offset - 1][1] != ToolStatus.IN_PROGRESS)
        ]
        activations.extend((index, slot, frames) for index in starts)
    if not activations:
        return REASONING
    started, slot, frames = max(activations, key=lambda entry: entry[0])
    if any(
        index > started
        and frame.effective_status
        in (ToolStatus.PENDING, ToolStatus.COMPLETED, ToolStatus.FAILED)
        for index, frame in frames
    ):
        return REASONING
    identities = [
        frame.step
        for _, frame in frames
        if frame.step in (Step.START, Step.IDENTITY, Step.REJECT_IDENTITY)
    ]
    name = (
        TOOLS[slot]
        if identities and identities[-1] != Step.REJECT_IDENTITY
        else UNKNOWN_ACTIVITY_TOOL_NAME
    )
    return tool_call_activity(name)


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
            match step:
                case Step.START:
                    update = band_activity_call(str(slot), TOOLS[slot], status)
                case Step.UPDATE:
                    update = update_tool_call(
                        str(slot), status=cast(ToolCallStatus | None, status)
                    )
                case Step.IDENTITY:
                    update = update_tool_call(str(slot)).model_copy(
                        update={"field_meta": band_activity_metadata(TOOLS[slot])}
                    )
                case Step.REJECT_IDENTITY:
                    update = update_tool_call(str(slot)).model_copy(
                        update={"field_meta": {"mcp": None}}
                    )
            await client.session_update(str(room), update)
        for current in range(2):
            assert recorders[current].activity.detail == expected_activity(
                histories[current]
            )
        for recorder, events in retired:
            assert recorder.events == events
