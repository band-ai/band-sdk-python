"""Generated lifecycle model for permission owners and running tools across room
generations."""

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
    TOOL_START = auto()
    TOOL_RESTART = auto()
    TOOL_END = auto()
    TOOL_DUPLICATE_END = auto()


@dataclass
class Generation:
    activity: TurnActivity = field(default_factory=TurnActivity)
    expected: set[int] = field(default_factory=set)
    selected: tuple[int, str] | None = None

    @property
    def expected_progress(self) -> AgentActivityDetail:
        return tool_call_activity(self.selected[1]) if self.selected else REASONING

    def hold(self, key: int, name: str) -> None:
        if not self.expected:
            self.selected = (key, name)

    def drop(self, key: int) -> None:
        if self.selected is not None and self.selected[0] == key:
            self.selected = None


class ActivityLifecycle:
    """Permission owners and running tools stay attached to the room generation
    they began in; the shown detail is a wait, else the newest running tool,
    else reasoning."""

    def __init__(self) -> None:
        self.rooms = [Generation(), Generation()]
        self.generations = list(self.rooms)
        self.active: dict[tuple[int, int], tuple[Generation, object, int]] = {}
        self.ended: list[tuple[Generation, object]] = []
        self.tools: dict[tuple[int, int], Generation] = {}
        self.ended_tools: dict[tuple[int, int], Generation] = {}
        self.next_id = 0

    def apply(self, step: Step, room: int, slot: int) -> None:
        current = self.rooms[room]
        key = (room, slot)
        if step == Step.BEGIN and key not in self.active:
            owner = current.activity.acquire_permission()
            current.expected.add(self.next_id)
            current.selected = None
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
        elif step == Step.TOOL_START and key not in self.tools:
            self.tools[key] = current
            self.start_tool(current, slot)
        elif step == Step.TOOL_RESTART and key in self.tools:
            self.start_tool(self.tools[key], slot)
        elif step == Step.TOOL_END and key in self.tools:
            generation = self.tools.pop(key)
            generation.activity.end(slot)
            generation.drop(slot)
            self.ended_tools[key] = generation
        elif (
            step == Step.TOOL_DUPLICATE_END
            and key not in self.tools
            and key in self.ended_tools
        ):
            self.ended_tools[key].activity.end(slot)
        self.assert_consistent()

    def start_tool(self, generation: Generation, slot: int) -> None:
        name = f"tool-{self.next_id}"
        self.next_id += 1
        generation.activity.start(slot, tool_call_activity(name))
        generation.hold(slot, name)

    def assert_consistent(self) -> None:
        for generation in self.generations:
            assert generation.activity.waiting == bool(generation.expected)
            assert generation.activity.progress == generation.expected_progress
            assert generation.activity.detail == (
                PERMISSION if generation.expected else generation.expected_progress
            )

    def finish(self) -> None:
        for room, slot in list(self.active):
            self.apply(Step.END, room, slot)
        for room, slot in list(self.tools):
            self.apply(Step.TOOL_END, room, slot)
        assert all(
            generation.activity.detail == REASONING for generation in self.generations
        )
