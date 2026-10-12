"""Helpers shared by the ExecutionContext interrupt/stop tests.

The reasoning cycle runs as a cancellable child task so a control signal can
abort just one turn without killing the room's process loop. The tests drive
``_process_event`` directly with a controllable ``on_execute`` so they can
cancel mid-cycle deterministically.
"""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage
from tests.conftest import make_message_event


async def assert_fresh_cycle_still_propagates_shutdown_cancel(
    ctx: ExecutionContext, msg_id: str
) -> None:
    """A cycle genuinely cancelled by shutdown (no new interrupt()) must
    propagate CancelledError, not get misclassified as an interrupt/stop via
    a leaked ``_interrupt_kind`` from whatever ran on ``ctx`` before it."""
    started = asyncio.Event()

    async def block(ctx, event):
        started.set()
        await asyncio.Event().wait()

    ctx._on_execute = block
    shutdown = asyncio.create_task(
        ctx._run_cycle(make_message_event(msg_id=msg_id), msg_id)
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
