from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from tests.framework_conformance.turnprobes import turn_input

pytest.importorskip("strands", reason="strands extra not installed")

import itertools

from band.adapters.strands import (
    TURN_DIAGNOSTICS_LOG,
    BandTurnHooks,
    StrandsAdapter,
)
from band.core.protocols import (
    AgentToolsProtocol,
)
from band.core.types import (
    PlatformMessage,
)
from band.testing import (
    FakeAgentTools,
    ToolTurn,
)

_INPUT_TOKENS_PER_CALL = 7

_OUTPUT_TOKENS_PER_CALL = 3

ROOM = "room-1"

SEND_TURN = ToolTurn("band_send_message", {"content": "hi", "mentions": ["@tester"]})


def _make_msg(room_id: str, content: str = "Hello") -> PlatformMessage:
    return PlatformMessage(
        id="msg-1",
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(UTC),
    )


async def _run_message(
    adapter: StrandsAdapter,
    tools: FakeAgentTools,
    room_id: str = ROOM,
    *,
    history: list | None = None,
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    is_session_bootstrap: bool = True,
) -> None:
    await adapter.on_message(
        msg=_make_msg(room_id),
        tools=cast("AgentToolsProtocol", tools),
        history=history or [],
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
    )


async def run_judged_turn(adapter: StrandsAdapter, tools: FakeAgentTools) -> None:
    """Run one turn through ``on_event``, where the shared turn verdict lives."""
    await adapter.on_event(turn_input(tools, msg=_make_msg(ROOM)))


def _tool_results(adapter: StrandsAdapter, room_id: str = ROOM) -> list[str]:
    """The tool outputs the model saw this turn, in order."""
    return [
        item["text"]
        for message in adapter._message_history[room_id]
        for block in message["content"]
        if "toolResult" in block
        for item in block["toolResult"]["content"]
        if "text" in item
    ]


def failure_diagnostics(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.args[1])
        for record in caplog.records
        if record.name == "band.adapters.strands"
        and record.msg == TURN_DIAGNOSTICS_LOG
        and isinstance(record.args, tuple)
    ]


def _alternates(history: list) -> bool:
    """Whether the transcript never puts two same-role turns in a row."""
    roles = [message["role"] for message in history]
    return all(first != second for first, second in itertools.pairwise(roles))


async def _turn_agent(adapter: StrandsAdapter) -> Any:
    """The Strands agent the adapter builds for one turn."""
    await adapter.on_started("Bot", "A bot")
    tools = FakeAgentTools()
    hooks = BandTurnHooks(tools, emit_execution=False, custom_effects={})
    return adapter._build_agent([], tools, hooks)
