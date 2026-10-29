"""Names-only room activity detail, the per-turn state that holds it, and the
observer a turn reports it through."""

from __future__ import annotations

from collections.abc import Hashable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from inspect import getattr_static
from typing import Protocol

from band_rest.types.agent_activity_detail import AgentActivityDetail

UNKNOWN_ACTIVITY_TOOL_NAME = "unknown"
TOOL_CALL = "tool_call"
REASONING = AgentActivityDetail(kind="reasoning")
MEMORY = AgentActivityDetail(kind="memory")
PERMISSION = AgentActivityDetail(kind="permission")


class ActivityObserver(Protocol):
    async def start(self, key: Hashable, activity: AgentActivityDetail) -> None: ...

    async def end(self, key: Hashable) -> None: ...

    def permission_wait(self) -> AbstractContextManager[None]: ...


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
    """What one turn is doing: the most recently started activity still running,
    else reasoning; a person's wait overrides both.

    Each running activity is held under its own key, so one that ends never
    hides another still running, whichever reporter owns either of them.
    """

    _running: dict[Hashable, AgentActivityDetail] = field(
        default_factory=dict, repr=False
    )
    _permission_owners: set[object] = field(default_factory=set, repr=False)

    @property
    def waiting(self) -> bool:
        return bool(self._permission_owners)

    def acquire_permission(self) -> object:
        """Own one permission operation independently of other waits."""
        owner = object()
        self._permission_owners.add(owner)
        return owner

    def release_permission(self, owner: object) -> None:
        self._permission_owners.discard(owner)

    def start(self, key: Hashable, activity: AgentActivityDetail) -> None:
        """Hold ``activity`` under ``key``; restarting a key renames it in place."""
        self._running[key] = names_only(activity)

    def end(self, key: Hashable) -> None:
        self._running.pop(key, None)

    @property
    def progress(self) -> AgentActivityDetail:
        return next(reversed(self._running.values()), REASONING)

    @property
    def detail(self) -> AgentActivityDetail:
        return PERMISSION if self.waiting else self.progress


def activity_observer_of(tools: object) -> ActivityObserver | None:
    """The turn observer bound to ``tools``, if it declares one."""
    # Only a declared attribute counts: a dynamic proxy (``__getattr__``, a
    # mock) would conjure an observer it never had. The second, dynamic read
    # resolves a declared property such as a wrapper's pass-through.
    if getattr_static(tools, "_activity_observer", None) is None:
        return None
    return getattr(tools, "_activity_observer", None)


def permission_wait(tools: object) -> AbstractContextManager[None]:
    """Keep permission activity on the turn captured by these tools."""
    observer = activity_observer_of(tools)
    return observer.permission_wait() if observer is not None else nullcontext()
