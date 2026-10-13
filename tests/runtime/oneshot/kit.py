"""Shared OneShotInvoker test helpers. The BandLink/REST fakes
(make_link_mock, platform_msg, ctx_item) live in tests/runtime/conftest.py.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel, Field

from band.adapters.anthropic import AnthropicAdapter
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability
from band.runtime.oneshot import (
    OneShotInvoker,
)
from tests.runtime.conftest import make_link_mock, platform_msg


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


class VaultCodeInput(BaseModel):
    """Look up the vault code for a topic."""

    topic: str = Field(description="Topic to look up")


class AcceptedWork:
    """An invoker whose adapter work is held, with every effect ordered."""

    def __init__(self, *, turns: int = 1) -> None:
        self.order: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.turns = turns
        self.link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        self.link.mark_processed.side_effect = self.record("ack")
        self.link.disconnect.side_effect = self.record("disconnect")
        self.adapter = make_adapter_mock()
        self.adapter.on_event.side_effect = self.held("turn")
        self.adapter.on_cleanup.side_effect = self.held("room cleanup")
        self.adapter.cleanup_all.side_effect = self.record("cleanup_all")
        self.invoker = OneShotInvoker(
            link=self.link, adapter=self.adapter, agent_id="agent-1"
        )
        self._entered = 0

    def record(self, name: str):
        async def effect(*args: object, **kwargs: object) -> bool:
            self.order.append(name)
            return True

        return effect

    def held(self, name: str):
        async def effect(*args: object, **kwargs: object) -> None:
            self.order.append(f"{name} start")
            self._entered += 1
            if self._entered == self.turns:
                self.started.set()
            await self.release.wait()
            self.order.append(f"{name} end")

        return effect

    def accept(self, body: dict[str, Any]) -> asyncio.Task[dict[str, Any]]:
        return asyncio.create_task(self.invoker.handle_event(body))


ROOM_REMOVED = {"event_type": "room_removed", "room_id": "r1", "payload": {}}


async def run_ready_callbacks() -> None:
    """Yield without wall-clock delay, long enough for an unblocked shutdown
    to reach disposal."""
    for _ in range(20):
        await asyncio.sleep(0)


class FilesAdapter(SimpleAdapter):
    """Bare SimpleAdapter declaring only Capability.FILES support."""

    SUPPORTED_CAPABILITIES = frozenset({Capability.FILES})

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        pass


class HeldCall:
    """An awaited call that waits for ``release`` and records its order."""

    def __init__(self, name: str, order: list[str], *, result: object = None):
        self.name = name
        self.order = order
        self.result = result
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, *args: object, **kwargs: object) -> object:
        self.entered.set()
        self.order.append(f"{self.name} started")
        await self.release.wait()
        self.order.append(f"{self.name} finished")
        return self.result


class SendEmailInput(BaseModel):
    """Send an email to an address."""

    to: str
