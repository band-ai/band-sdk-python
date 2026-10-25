"""Record what a turn's activity observer is told, without reporting it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol, TypeVar

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.runtime.activity import ActivityObserver, TurnActivity


class ObservableTools(Protocol):
    _activity_observer: ActivityObserver | None


_Tools = TypeVar("_Tools", bound=ObservableTools)

SHOWN_REASONING: dict[str, Any] = {"kind": "reasoning"}
SHOWN_MEMORY: dict[str, Any] = {"kind": "memory"}


def shown_tool(name: str) -> dict[str, Any]:
    """A recorded ``tool_call`` observation for ``name``."""
    return {"kind": "tool_call", "name": name}


class ActivityRecorder:
    """An ``ActivityObserver`` that keeps every observation in order."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.activity = TurnActivity()

    def observing(self, tools: _Tools) -> _Tools:
        """Bind this recorder as the turn observer of ``tools``."""
        tools._activity_observer = self
        return tools

    async def update(self, activity: AgentActivityDetail) -> None:
        self.activity.apply(activity=activity)
        self.events.append(activity.model_dump(exclude_none=True))

    @contextmanager
    def permission_wait(self) -> Iterator[None]:
        owner = self.activity.acquire_permission()
        try:
            self._record_wait()
            yield
        finally:
            self.activity.release_permission(owner)
            self._record_wait()

    def _record_wait(self) -> None:
        self.events.append({"waiting": self.waiting})

    @property
    def waiting(self) -> bool:
        """Whether any permission operation is still waiting."""
        return self.activity.waiting

    def assert_completed_tool(self, name: str) -> None:
        assert self.events == [shown_tool(name), SHOWN_REASONING]
