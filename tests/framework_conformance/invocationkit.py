"""A real batch-capable adapter and turn inputs for the invocation-observer tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing.fake_tools import FakeAgentTools

ROOM_ID = "room-1"


def original(message_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id=ROOM_ID,
        content=message_id,
        sender_id="alice",
        sender_type="User",
        sender_name=None,
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def turn_input(*messages: PlatformMessage, room_id: str = ROOM_ID) -> AgentInput:
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=FakeAgentTools(room_id=room_id),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=room_id,
    )


class DecliningAdapter(SimpleAdapter[Any]):
    """A batch-capable adapter whose model input builder declines every turn."""

    supports_batch_input = True

    def __init__(self) -> None:
        super().__init__()
        self.built: list[tuple[str, ...]] = []

    async def on_message(self, msg: PlatformMessage, *args: Any, **kwargs: Any) -> None:
        await self.on_turn((msg,), *args, **kwargs)

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.built.append(tuple(message.id for message in messages))
        await tools.no_reply("nothing owed")


class BusyAdapter(DecliningAdapter):
    """A batch-capable adapter whose room is busy for its first attempt, then free."""

    def __init__(self) -> None:
        super().__init__()
        self.busy = True

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self.busy:
            self.busy = False
            raise InputNotAccepted("The room already has an active turn")
        await super().on_turn(messages, tools, *args, **kwargs)
