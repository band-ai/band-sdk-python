"""Generated lifecycle model for permission owners across room generations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.runtime.activity import (
    PERMISSION,
    REASONING,
    TurnActivity,
    tool_call_activity,
)


class Step(StrEnum):
    BEGIN = auto()
    END = auto()
    DUPLICATE_END = auto()
    REPLACE = auto()
    PROGRESS = auto()


@dataclass
class Generation:
    activity: TurnActivity = field(default_factory=TurnActivity)
    expected: set[int] = field(default_factory=set)
    progress: AgentActivityDetail = REASONING


class PermissionLifecycle:
    """Logical request owners remain attached to the room generation they began in."""

    def __init__(self) -> None:
        self.rooms = [Generation(), Generation()]
        self.generations = list(self.rooms)
        self.active: dict[tuple[int, int], tuple[Generation, object, int]] = {}
        self.ended: list[tuple[Generation, object]] = []
        self.next_id = 0

    def apply(self, step: Step, room: int, slot: int) -> None:
        current = self.rooms[room]
        key = (room, slot)
        if step == Step.BEGIN and key not in self.active:
            owner = current.activity.acquire_permission()
            current.expected.add(self.next_id)
            self.active[key] = (current, owner, self.next_id)
            self.next_id += 1
        elif step == Step.END and key in self.active:
            generation, owner, request = self.active.pop(key)
            generation.activity.release_permission(owner)
            generation.expected.remove(request)
            self.ended.append((generation, owner))
        elif step == Step.DUPLICATE_END and self.ended:
            generation, owner = self.ended[slot % len(self.ended)]
            generation.activity.release_permission(owner)
        elif step == Step.REPLACE:
            replacement = Generation()
            self.rooms[room] = replacement
            self.generations.append(replacement)
        elif step == Step.PROGRESS:
            current.progress = tool_call_activity(f"tool-{slot}")
            current.activity.apply(activity=current.progress)
        self.assert_consistent()

    def assert_consistent(self) -> None:
        for generation in self.generations:
            assert generation.activity.waiting == bool(generation.expected)
            assert generation.activity.detail == (
                PERMISSION if generation.expected else generation.progress
            )

    def finish(self) -> None:
        for room, slot in list(self.active):
            self.apply(Step.END, room, slot)
        assert all(not generation.activity.waiting for generation in self.generations)
