"""Record the optional producer observation seam without scheduling reports."""

from __future__ import annotations

from typing import Any

from band_rest.types.agent_activity_detail import AgentActivityDetail


class ActivityRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    @property
    def waiting(self) -> bool:
        return next(
            (event["waiting"] for event in reversed(self.events) if "waiting" in event),
            False,
        )

    async def update(self, activity: AgentActivityDetail) -> None:
        self.events.append(activity.model_dump(exclude_unset=True))

    async def set_manual_wait(self, waiting: bool) -> None:
        self.events.append({"waiting": waiting})

    def assert_completed_tool(self, name: str) -> None:
        assert self.events == [
            {"kind": "tool_call", "name": name},
            {"kind": "reasoning"},
        ]
