"""Cancellation before handler entry prevents effects and preserves FIFO."""

from __future__ import annotations

import pytest

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import MailboxSubmit, cancel_at_handoff


class TestHandoffCancellation:
    @pytest.mark.parametrize(
        "submit", [RoomMailbox.ask, RoomMailbox.spawn], ids=["ask", "spawn"]
    )
    async def test_cancelled_waiter_prevents_handler_effects(
        self, submit: MailboxSubmit
    ) -> None:
        await cancel_at_handoff(RoomMailbox(), "room-1", submit)
