"""Shared helpers for the Codex adapter flow tests."""

from __future__ import annotations

from typing import Any

from band.core.types import AgentInput, HistoryProvider
from band.integrations.codex import RpcEvent
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import make_platform_message
from tests.adapters.codexturns import final_text, tool_call_request, turn_completed


def agent_input(
    content: str,
    tools: FakeAgentTools,
    *,
    raw_history: list[dict[str, Any]] | None = None,
    room_id: str = "room-1",
    is_session_bootstrap: bool = True,
) -> AgentInput:
    return AgentInput(
        msg=make_platform_message(room_id=room_id, content=content),
        tools=tools,
        history=HistoryProvider(raw=raw_history or []),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
    )


def answered_turn() -> list[RpcEvent]:
    """A turn explicitly completed through a Band decline tool."""
    return [
        tool_call_request(1, "band_no_reply"),
        final_text("Done."),
        turn_completed(),
    ]
