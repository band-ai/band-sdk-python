"""The approval room's timeout diagnostic must print even when the platform is down."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.e2e.baseline.agents import Adapter
from tests.e2e.baseline.smoke.samples import approvalroom
from tests.e2e.baseline.smoke.samples.approvalroom import ApprovalRoom
from tests.e2e.baseline.timeouts import SlowTurnBudget


async def stall() -> None:
    await asyncio.sleep(3600)


def room_with(*, list_messages: AsyncMock, usage: AsyncMock) -> ApprovalRoom:
    capture = MagicMock()
    capture.messages.since.return_value = []
    capture.usage = usage
    dialect = MagicMock()
    dialect.find_requests.return_value = []
    dialect.settled.return_value = False
    user_ops = MagicMock()
    user_ops.list_messages = list_messages
    return ApprovalRoom(
        agent=MagicMock(id="agent-1"),
        adapter_id=Adapter.OPENCODE,
        room_id="room-1",
        capture=capture,
        dialect=dialect,
        user_ops=user_ops,
        budget=SlowTurnBudget(deadline_s=1.0, extra_s=0),
    )


@pytest.mark.parametrize(
    "failing_read",
    [AsyncMock(side_effect=stall), AsyncMock(side_effect=ConnectionError("down"))],
    ids=["stalled", "raising"],
)
async def test_closing_state_marks_unreadable_platform_fields_unknown(
    failing_read: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approvalroom, "DIAGNOSTIC_READ_TIMEOUT_S", 0.05)
    room = room_with(list_messages=failing_read, usage=failing_read)

    state = await room._closing_state(0, [], "DONE")

    assert "settled=False" in state
    assert "durable_settled=?" in state
    assert "usage_recorded=?" in state
