"""Control-first delivery at the adapter test boundary."""

from __future__ import annotations

from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, PlatformMessage


async def deliver_event(adapter: SimpleAdapter[Any], inp: AgentInput) -> None:
    if not await adapter.try_handle_control(inp.msg, inp.tools):
        await adapter.on_event(inp)


async def deliver_message(
    adapter: SimpleAdapter[Any],
    msg: PlatformMessage,
    tools: AgentToolsProtocol,
    history: Any,
    participants_msg: str | None,
    contacts_msg: str | None,
    *,
    is_session_bootstrap: bool,
    room_id: str,
) -> None:
    if not await adapter.try_handle_control(msg, tools):
        await adapter.on_message(
            msg,
            tools,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )
