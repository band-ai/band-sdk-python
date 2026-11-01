"""Names-only room activity detail, the per-turn state that holds it, and the
observer a turn reports it through."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Hashable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from inspect import getattr_static
from typing import Any, Protocol

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


@dataclass(frozen=True)
class ActivitySelection:
    """The single ordinary detail currently selected for display."""

    key: Hashable
    detail: AgentActivityDetail
    origin: Hashable | None = None


def select_activity(
    key: Hashable, detail: AgentActivityDetail, origin: Hashable | None = None
) -> ActivitySelection:
    return ActivitySelection(key, names_only(detail), origin)


def finish_activity(
    current: ActivitySelection | None, key: Hashable
) -> ActivitySelection | None:
    return None if current is not None and current.key == key else current


@dataclass
class TurnActivity:
    """One current detail and independent manual waits, without restoration."""

    _current: ActivitySelection | None = field(default=None, repr=False)
    _permission_owners: dict[object, Hashable | None] = field(
        default_factory=dict, repr=False
    )

    @property
    def waiting(self) -> bool:
        return bool(self._permission_owners)

    def acquire_permission(self, origin: Hashable | None = None) -> object:
        owner = object()
        self._permission_owners[owner] = origin
        self._current = None
        return owner

    def release_permission(self, owner: object) -> None:
        self._permission_owners.pop(owner, None)

    def waiting_for(self, eligible: Collection[Hashable] | None) -> bool:
        return any(
            eligible is None or origin in eligible
            for origin in self._permission_owners.values()
        )

    def retire(self, origin: Hashable) -> bool:
        """Discard revoked callbacks' selection and leases once."""
        selected = self._current is not None and self._current.origin == origin
        if selected:
            self._current = None
        leases = tuple(
            key for key, owner in self._permission_owners.items() if owner == origin
        )
        for key in leases:
            del self._permission_owners[key]
        return selected or bool(leases)

    def start(
        self,
        key: Hashable,
        activity: AgentActivityDetail,
        *,
        origin: Hashable | None = None,
        eligible: Collection[Hashable] | None = None,
    ) -> None:
        if not self.waiting_for(eligible):
            self._current = select_activity(key, activity, origin)

    def rename(self, key: Hashable, activity: AgentActivityDetail) -> None:
        """An identity patch cannot reselect a displaced operation."""
        if self._current is not None and self._current.key == key:
            self._current = select_activity(key, activity, self._current.origin)

    def end(self, key: Hashable) -> None:
        self._current = finish_activity(self._current, key)

    @property
    def progress(self) -> AgentActivityDetail:
        return self._current.detail if self._current is not None else REASONING

    def detail_for(self, eligible: Collection[Hashable] | None) -> AgentActivityDetail:
        if self.waiting_for(eligible):
            return PERMISSION
        if self._current is None or (
            eligible is not None and self._current.origin not in eligible
        ):
            return REASONING
        return self._current.detail

    @property
    def detail(self) -> AgentActivityDetail:
        return self.detail_for(None)


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


def release_request_permission(
    lease: AbstractContextManager[None], task: asyncio.Task[Any]
) -> None:
    lease.__exit__(None, None, None)


def retain_request_permission(tools: object) -> None:
    """Keep a manual wait through its existing RPC task's response write."""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("A manual permission request requires an owned request task")
    lease = permission_wait(tools)
    lease.__enter__()
    task.add_done_callback(partial(release_request_permission, lease))
