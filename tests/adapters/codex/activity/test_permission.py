from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.adapters.codex import CodexCommand
from band.testing.activity import ActivityRecorder
from tests.adapters.codex.kit import CodexApprovalRoom


class TestCodexPermissionActivity:
    async def test_the_room_shows_permission_until_its_last_approval_is_answered(
        self,
        approval_room: Callable[..., Awaitable[CodexApprovalRoom]],
        recorder: ActivityRecorder,
    ) -> None:
        room = await approval_room()
        recorder.observing(room.tools)
        async with room.prompted(count=2):
            first = room.ask("first", request_id=1)
            second = room.ask("second", request_id=2)
        assert recorder.waiting

        first_token, second_token = room.approval_tokens
        await room.reply(CodexCommand.APPROVE, first_token)
        assert await first == "accept"
        assert recorder.waiting

        await room.reply(CodexCommand.APPROVE, second_token)
        assert await second == "accept"
        assert not recorder.waiting
