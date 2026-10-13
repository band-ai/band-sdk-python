"""A retired CLI's late callbacks never act for the room's next session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext

from band.adapters.claude_sdk import RETIRED_SESSION_DENIAL
from tests.adapters.claude_sdk.helpers import ClaudeRoom, with_approvals

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class TestRetiredCliAuthority:
    async def test_a_permission_request_from_a_left_rooms_cli_is_denied(
        self, claude_room: OpenRoom
    ) -> None:
        """A rejoined room starts counting residencies again, so only the session
        object tells the old CLI's late request apart; it never reaches the room."""
        room = await claude_room(with_approvals())
        room.claude.script([room.model_reply("first")], [room.model_reply("second")])
        await room.send("hi")
        await room.leave()
        await room.send("back again")
        old_cli, _ = room.claude.sessions
        assert old_cli.options.can_use_tool is not None

        decision = await old_cli.options.can_use_tool(
            "Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="late")
        )

        assert decision == PermissionResultDeny(message=RETIRED_SESSION_DENIAL)
        assert room.chat == ["first", "second"]
