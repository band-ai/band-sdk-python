"""Record what a turn's activity observer is told, without reporting it."""

from __future__ import annotations

from typing import Any

from band_rest.types.agent_activity_detail import AgentActivityDetail


class ActivityRecorder:
    """An ``ActivityObserver`` that keeps every observation in order."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def update(self, activity: AgentActivityDetail) -> None:
        self.events.append(activity.model_dump(exclude_none=True))

    async def set_manual_wait(self, waiting: bool) -> None:
        self.events.append({"waiting": waiting})

    def assert_completed_tool(self, name: str) -> None:
        assert self.events == [
            {"kind": "tool_call", "name": name},
            {"kind": "reasoning"},
        ]
