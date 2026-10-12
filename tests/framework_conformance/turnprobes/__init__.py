"""The shape every turn-outcome probe shares.

A probe runs one turn through an adapter's real ``on_event`` with that
adapter's existing scripted model following a ``TurnScript``, against the
``FakeAgentTools`` it is handed. It lets the verdict's exception propagate, so
``test_turn_outcome`` sees exactly what the runtime would.

Framework imports stay inside each probe function: not every lane's venv has
every framework installed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import (
    TOOL_DEFINITIONS,
    ToolCallOutcome,
    TurnEffect,
    serialize_tool_result,
)
from band.testing.fake_tools import FakeAgentTools

ROOM_ID = "room-turn"


def undeclared(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Leave a custom tool's effect undeclared, so its call only observes."""
    return handler


#: A custom tool declared as real work completes the turn; an undeclared one
#: only observes, so the turn still owes a reply.
CUSTOM_TOOL_DECLARATIONS = [
    pytest.param(declares_turn_effect(TurnEffect.ACT), True, id="declared-act"),
    pytest.param(undeclared, False, id="undeclared-observes"),
]
ALICE: dict[str, Any] = {
    "id": "user-alice",
    "name": "Alice",
    "type": "User",
    "handle": "alice",
    "role": "member",
    "status": "active",
}


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnScript:
    """What the scripted model does in one turn: these Band tool calls in
    order, then this final text (empty for a tool-only ending)."""

    tool_calls: tuple[ToolCall, ...] = ()
    final_text: str = ""


@dataclass(frozen=True)
class TurnOutcomeProbe:
    """``run`` drives one scripted turn. ``settle``, for an adapter with a
    control path, sends a message the adapter answers itself (a status or
    busy reply) without running the model. ``relays`` marks an adapter that
    posts the model's final text when no tool replied."""

    run: Callable[[TurnScript, FakeAgentTools], Awaitable[None]]
    settle: Callable[[FakeAgentTools], Awaitable[None]] | None = None
    relays: bool = False


class DispatchingFakeTools(FakeAgentTools):
    """Runs each dispatched Band tool through the fake's own method, as
    ``AgentTools`` does, so a scripted ``band_send_message`` really posts."""

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        method = getattr(self, TOOL_DEFINITIONS[tool_name].method_name)
        return ToolCallOutcome(
            value=serialize_tool_result(await method(**arguments)), ok=True
        )


def turn_tools() -> FakeAgentTools:
    return DispatchingFakeTools(room_id=ROOM_ID, participants=[ALICE])


def user_message(content: str = "@agent what is the vault code?") -> PlatformMessage:
    return PlatformMessage(
        id="msg-turn",
        room_id=ROOM_ID,
        content=content,
        sender_id=ALICE["id"],
        sender_type=ALICE["type"],
        sender_name=ALICE["name"],
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def turn_input(tools: FakeAgentTools, msg: PlatformMessage | None = None) -> AgentInput:
    """The ``AgentInput`` the runtime hands ``on_event`` for one user message."""
    msg = msg or user_message()
    return AgentInput(
        msg=msg,
        tools=tools,
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=msg.room_id,
    )
