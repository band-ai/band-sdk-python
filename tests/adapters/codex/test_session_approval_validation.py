"""Codex adapter: session approval validation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tests.adapters.codex.kit import (
    DECLINED,
    ROOM_ID,
    CodexRoom,
)
from tests.adapters.codexturns import (
    event_request,
    turn_completed,
)


class TestSessionApprovalValidation:
    """Guards that stop /approve-session from storing bogus session keys."""

    async def test_approve_session_is_refused_for_a_change_with_no_paths(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        """A file change naming no paths has no signature to match, so
        /approve-session is refused -- keying on the method alone would
        auto-approve every future file change -- and the approval stays open
        for a one-shot answer."""
        room = await codex_room(
            event_request(15, "item/fileChange/requestApproval", {}),
            turn_completed(),
        )

        await room.send("run")
        await room.send(f"/approve-session {room.approval_token}")
        await room.send(f"/decline {room.approval_token}")
        await room.settled()

        assert room.client.responses == [(15, DECLINED)]
        assert not room.adapter._session_approved.get(ROOM_ID)
        assert sum("cannot be resolved as session-level" in m for m in room.chat) == 1
