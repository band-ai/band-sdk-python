"""Tools helpers for delivery-flow tests — always production-shaped."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from band.core.backends.observing import ObservingTools
from band.core.exceptions import BandToolError
from band.runtime.tools import (
    LEGACY_CREATE_AGENT_CHAT_MESSAGE,
    ToolCallOutcome,
    is_room_posting_tool,
)
from band.testing import FakeAgentTools, RecordedToolCall

from tests.baseline.delivery.scenarios import (
    LEGACY_POST_TEXT,
    TOOL_POST_TEXT,
    DeliveryScenario,
)

ROOM_ID = "room-1"

ToolsT = TypeVar("ToolsT", bound=FakeAgentTools)


@dataclass(frozen=True, slots=True)
class BandSendMessageArgs:
    """Arguments for a room-posting tool call in delivery fixtures."""

    content: str
    mentions: tuple[str, ...] = ("Ada",)

    def to_arguments(self) -> dict[str, Any]:
        return {"content": self.content, "mentions": list(self.mentions)}


VIA_TOOL = BandSendMessageArgs(content=TOOL_POST_TEXT)
VIA_LEGACY = BandSendMessageArgs(content=LEGACY_POST_TEXT)


def failing_room_post_tools(
    base: type[ToolsT] = FakeAgentTools,  # type: ignore[assignment]
    *,
    error_message: str = "upstream 500",
    room_id: str = ROOM_ID,
) -> ToolsT:
    """``base`` subclass whose room-posting tools soft-fail without raising."""

    class Failing(base):  # type: ignore[valid-type,misc]
        async def send_message(
            self,
            content: str,
            mentions: list[str] | list[dict[str, str]] | None = None,
        ) -> dict[str, Any]:
            # A room-posting tool can also reach the platform through this
            # method (an MCP server resolving the tool to it), where failure is
            # a raise rather than a soft outcome. Only the model's post is
            # rejected — the adapter's text fallback must still be able to
            # speak, which is the whole point of the row.
            if content == TOOL_POST_TEXT:
                raise BandToolError(f"Error executing send_message: {error_message}")
            return await super().send_message(content, mentions)

        async def execute_tool_call_structured(
            self, tool_name: str, arguments: dict[str, Any]
        ) -> ToolCallOutcome:
            if is_room_posting_tool(tool_name):
                self.tool_calls.append(
                    RecordedToolCall(tool_name=tool_name, arguments=arguments)
                )
                return ToolCallOutcome(
                    value=f"Error executing {tool_name}: {error_message}",
                    ok=False,
                    error_message=error_message,
                )
            return await super().execute_tool_call_structured(tool_name, arguments)

    Failing.__name__ = f"Failing{base.__name__}"
    Failing.__qualname__ = Failing.__name__
    return Failing(room_id=room_id)  # type: ignore[return-value]


def observed_tools(
    *,
    room_id: str = ROOM_ID,
    fail_room_post: bool = False,
) -> tuple[ObservingTools, FakeAgentTools]:
    """Return ``(proxy, inner)``. Adapters receive the proxy; asserts read the inner."""
    inner: FakeAgentTools = (
        failing_room_post_tools(room_id=room_id)
        if fail_room_post
        else FakeAgentTools(room_id=room_id)
    )
    return ObservingTools(_inner=inner), inner


def tools_for_scenario(
    scenario: DeliveryScenario,
    base: type[ToolsT] = FakeAgentTools,  # type: ignore[assignment]
    *,
    room_id: str = ROOM_ID,
    fail_error: str = "upstream 500",
) -> ToolsT:
    """Inner tools for a scenario: soft-failing when the row is ``POST_FAIL``."""
    if scenario.fails_room_post:
        return failing_room_post_tools(base, error_message=fail_error, room_id=room_id)
    return base(room_id=room_id)  # type: ignore[return-value]


async def apply_legacy_room_post(tools: ObservingTools) -> None:
    """Completed legacy band-mcp spelling must mint a receipt."""
    await tools.execute_tool_call_structured(
        LEGACY_CREATE_AGENT_CHAT_MESSAGE, VIA_LEGACY.to_arguments()
    )
