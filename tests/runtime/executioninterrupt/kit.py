"""Helpers shared by the ExecutionContext interrupt/stop tests.

The reasoning cycle runs as a cancellable child task so a control signal can
abort just one turn without killing the room's process loop. The tests drive
``_process_event`` directly with a controllable ``on_execute`` so they can
cancel mid-cycle deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.client.rest import AsyncRestClient
from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import make_message_event
from tests.runtime.helpers import AGENT_ID, ROOM_ID


async def assert_fresh_cycle_still_propagates_shutdown_cancel(
    ctx: ExecutionContext, msg_id: str
) -> None:
    """A cycle genuinely cancelled by shutdown (no new interrupt()) must
    propagate CancelledError, not get misclassified as an interrupt/stop left
    over from whatever ran on ``ctx`` before it."""
    started = asyncio.Event()

    async def block(ctx, event):
        started.set()
        await asyncio.Event().wait()

    ctx._on_execute = block
    shutdown = asyncio.create_task(
        ctx._process_event(make_message_event(msg_id=msg_id))
    )
    await started.wait()
    shutdown.cancel()

    with pytest.raises(asyncio.CancelledError):
        await shutdown


def backlog_message(msg_id: str = "msg-bk") -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id="room-123",
        content="hi",
        sender_id="user-1",
        sender_type="User",
        sender_name="User One",
        message_type="text",
        metadata={},
        created_at=None,
    )


def auxiliary_context(
    rest: AsyncRestClient,
    handler: Callable[[ExecutionContext, Any], Awaitable[None]],
    *,
    observer: Callable[[ExecutionContext, TurnScope], Awaitable[None]] | None = None,
    max_cycle_seconds: float | None = None,
    execution_type: type[ExecutionContext] = ExecutionContext,
) -> ExecutionContext:
    link = BandLink(agent_id=AGENT_ID, api_key="test-key")
    link.rest = rest
    return execution_type(
        ROOM_ID,
        link,
        handler,
        agent_id=AGENT_ID,
        on_platform_stop=observer,
        config=SessionConfig(
            enable_context_hydration=False,
            enable_working_state=False,
            max_message_retries=1,
            max_cycle_seconds=max_cycle_seconds,
        ),
    )


class ClosingExecution(ExecutionContext):
    waiting_for_idle: asyncio.Event

    async def _wait_for_idle(self, timeout: float) -> bool:
        self.waiting_for_idle.set()
        return await super()._wait_for_idle(timeout)
