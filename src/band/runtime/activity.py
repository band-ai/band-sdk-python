"""Names-only room activity detail, the per-turn state that holds it, and the
observer a turn reports it through."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import getattr_static
from typing import Protocol

from band_rest.types.agent_activity_detail import AgentActivityDetail

UNKNOWN_ACTIVITY_TOOL_NAME = "unknown"
TOOL_CALL = "tool_call"
REASONING = AgentActivityDetail(kind="reasoning")
MEMORY = AgentActivityDetail(kind="memory")
PERMISSION = AgentActivityDetail(kind="permission")


class ActivityObserver(Protocol):
    async def update(self, activity: AgentActivityDetail) -> None: ...

    async def set_manual_wait(self, waiting: bool) -> None: ...


def tool_call_activity(name: str | None) -> AgentActivityDetail:
    """The platform requires a name on every ``tool_call`` detail."""
    return AgentActivityDetail(kind=TOOL_CALL, name=name or UNKNOWN_ACTIVITY_TOOL_NAME)


def names_only(activity: AgentActivityDetail) -> AgentActivityDetail:
    """Keep only what the room may see: the kind and, for a tool, its name."""
    if activity.kind == TOOL_CALL:
        return tool_call_activity(activity.name)
    return AgentActivityDetail(kind=activity.kind)


@dataclass
class TurnActivity:
    """What one turn is doing; a person's wait overrides its progress."""

    progress: AgentActivityDetail = REASONING
    waiting: bool = False

    @property
    def detail(self) -> AgentActivityDetail:
        return PERMISSION if self.waiting else self.progress

    def apply(
        self,
        *,
        activity: AgentActivityDetail | None = None,
        waiting: bool | None = None,
    ) -> None:
        if activity is not None:
            self.progress = names_only(activity)
        if waiting is not None:
            self.waiting = waiting


def activity_observer_of(tools: object) -> ActivityObserver | None:
    """The turn observer bound to ``tools``, if it declares one."""
    # Only a declared attribute counts: a dynamic proxy (``__getattr__``, a
    # mock) would conjure an observer it never had. The second, dynamic read
    # resolves a declared property such as a wrapper's pass-through.
    if getattr_static(tools, "_activity_observer", None) is None:
        return None
    return getattr(tools, "_activity_observer", None)


async def set_permission_wait(tools: object, waiting: bool) -> None:
    if observer := activity_observer_of(tools):
        await observer.set_manual_wait(waiting)
