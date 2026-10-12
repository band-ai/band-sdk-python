"""Shared OneShotInvoker test helpers. The BandLink/REST fakes
(make_link_mock, platform_msg, ctx_item) live in tests/runtime/conftest.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band.adapters.anthropic import AnthropicAdapter
from band.runtime.oneshot import (
    OneShotInvoker,
)


def make_adapter_mock() -> MagicMock:
    """A fake adapter that records the AgentInput it was run with as a plain
    ``received_input`` attribute — reading that is what a test should assert
    on, rather than reaching into ``on_event.call_args.args[0]``. Still an
    AsyncMock underneath, so ``on_event.assert_awaited_once()`` etc. keep
    working unchanged.
    """
    adapter = MagicMock()
    adapter.received_input = None

    async def _capture(inp: Any) -> None:
        adapter.received_input = inp

    adapter.try_handle_control = AsyncMock(return_value=False)
    adapter.on_started = AsyncMock()
    adapter.on_event = AsyncMock(side_effect=_capture)
    adapter.on_cleanup = AsyncMock()
    adapter.cleanup_all = AsyncMock()
    return adapter


async def make_invoker(
    link: MagicMock,
    adapter: MagicMock | AnthropicAdapter | None = None,
    *,
    agent_id: str = "agent-1",
    drain_cap: int = 50,
    history_page_cap: int = 20,
) -> OneShotInvoker:
    invoker = OneShotInvoker(
        link=link,
        adapter=adapter or make_adapter_mock(),
        agent_id=agent_id,
        drain_cap=drain_cap,
        history_page_cap=history_page_cap,
    )
    await invoker.startup()
    return invoker


def msg_body(
    *,
    msg_id: str = "msg-1",
    room_id: str = "room-1",
    sender_id: str = "user-1",
    sender_type: str = "User",
    content: str = "@bot hello",
    agent_id: str = "agent-1",
) -> dict[str, Any]:
    return {
        "event_type": "message_created",
        "agent_id": agent_id,
        "room_id": room_id,
        "payload": {
            "id": msg_id,
            "content": content,
            "sender_id": sender_id,
            "sender_type": sender_type,
            "message_type": "user",
            "inserted_at": "2026-05-21T10:00:00Z",
            "updated_at": "2026-05-21T10:00:00Z",
        },
    }
