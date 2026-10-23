"""Room-visible activity detail and the choice that governs tool names.

A tool name reaches the room the same way its narration does: an agent that
does not report tool calls must not leak their names through the live
indicator. Every reporter funnels through the execution context's observer,
so the decision is applied once there and never per adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.core.types import Emit

if TYPE_CHECKING:
    from band.runtime.activity import ActivityObserver


class ToolNamesObserver:
    """Hide tool names while leaving every other activity detail intact.

    Permission waits and memory activity name no content, so they report
    whatever the tool-name choice is. ``hidden`` is consulted at report time,
    never bound at construction: an observer attached before the adapter's
    emit sync must still follow the turn's live choice.
    """

    def __init__(
        self, inner: ActivityObserver, hidden: Callable[[], bool] = lambda: True
    ) -> None:
        self._inner = inner
        self._hidden = hidden

    async def update(self, activity: AgentActivityDetail) -> None:
        if activity.kind == "tool_call" and self._hidden():
            await self._inner.update(AgentActivityDetail(kind="reasoning"))
            return
        await self._inner.update(activity)

    async def set_manual_wait(self, waiting: bool) -> None:
        await self._inner.set_manual_wait(waiting)


def sync_activity_tool_names(tools: object, emit: Collection[Emit]) -> None:
    """Push the adapter's tool-narration choice onto the bound context.

    ``Emit.TOOL_CALLS`` absent means the room sees ``reasoning`` where a tool
    runs. Tools without an execution context (fakes, unbound instances) have
    no room indicator to gate.
    """
    ctx = getattr(tools, "_ctx", None)
    setter = getattr(ctx, "set_activity_tool_names", None)
    if setter is not None:
        setter(Emit.TOOL_CALLS in emit)
