"""Adapter peer and inputs for activity independent of tool narration."""

from __future__ import annotations

from collections.abc import Sequence

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import BatchInputAdapter
from band.core.types import AgentInput, Emit, HistoryProvider, PlatformMessage
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import start_memory_activity, start_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, MEMORY_BODY, REASONING_BODY, tool_body
from tests.runtime.activityownership.kit import ActivityPlatform

ACTIVITY_TOOL_NAME = "Bash"
EXPECTED_ACTIVITY_BODIES = [
    REASONING_BODY,
    MEMORY_BODY,
    tool_body(ACTIVITY_TOOL_NAME),
    IDLE_BODY,
]


class ActivityToolAdapter(BatchInputAdapter[HistoryProvider]):
    """Report one tool activity through the shared adapter turn entry."""

    SUPPORTED_EMIT = frozenset({Emit.TOOL_CALLS})

    def __init__(
        self, activity_platform: ActivityPlatform, *, emit: tuple[Emit, ...]
    ) -> None:
        super().__init__(emit=emit)
        self._activity_platform = activity_platform

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: HistoryProvider,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await self._activity_platform.shown(start_memory_activity(tools, "memory"))
        await self._activity_platform.shown(
            start_tool_activity(tools, "tool", ACTIVITY_TOOL_NAME)
        )
        tools.turn.settle()


def turn_input(
    ctx: ExecutionContext, messages: tuple[PlatformMessage, ...]
) -> AgentInput:
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=AgentTools.from_context(ctx),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id=ctx.room_id,
    )
