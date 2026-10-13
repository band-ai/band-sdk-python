"""A room's CLI options bind the room, its workspace and its residency."""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
)
from claude_agent_sdk.types import (
    CanUseTool,
)

from band.integrations.claude_sdk.session_manager import (
    ClaudeRoomSession,
    ClaudeSessionManager,
)
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    allow_everything,
    thinking,
)


class TestRoomOptions:
    async def test_room_options_bind_the_room_its_workspace_and_its_residency(
        self, claude: FakeClaude, tmp_path: Path
    ) -> None:
        bindings: list[tuple[str, int]] = []

        def bind(session: ClaudeRoomSession, generation: int) -> CanUseTool:
            bindings.append((session.room_id, generation))
            return allow_everything(session, generation)

        manager = ClaudeSessionManager(
            ClaudeAgentOptions(model="claude-fake", mcp_servers={}),
            can_use_tool_factory=bind,
            mcp_servers_factory=lambda room_id: {
                "band": {"type": "http", "url": room_id}
            },
        )
        base = manager.base_options
        claude.script(thinking("hi"))
        session = manager.room("room-a")
        session.adopt("sess-earlier")
        await exchange(session, "hi")
        await manager.stop()

        [cli] = claude.sessions
        assert cli.options.resume == "sess-earlier"
        assert cli.options.mcp_servers == {"band": {"type": "http", "url": "room-a"}}
        assert cli.options.model == base.model
        assert base.resume is None and base.mcp_servers == {}
        assert bindings == [("room-a", 1)]
