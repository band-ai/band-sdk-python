"""A failed start whose close is refused keeps its process, and the next
start retries closing that same process before starting another."""

from __future__ import annotations

import re

import pytest
from claude_agent_sdk import CLIConnectionError

from tests.adapters.claude_sdk.fakecli import CLEANUP_FAILED, STARTUP_EXITED, FakeClaude
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
    thoughts,
)


class TestStartupCleanup:
    async def test_failed_startup_cleanup_is_retried_on_the_same_process(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        session = manager.room("room-a")
        claude.refuse_connect = claude.refuse_close = True

        with pytest.raises(CLIConnectionError, match=re.escape(STARTUP_EXITED)):
            await exchange(session, "hi")
        claude.refuse_connect = False
        with pytest.raises(RuntimeError, match=re.escape(CLEANUP_FAILED)):
            await exchange(session, "hi")
        assert manager.has_session("room-a")
        assert len(claude.sessions) == 1

        claude.refuse_close = False
        claude.script(thinking("started"))
        assert thoughts(await exchange(session, "hi")) == ["started"]
        await manager.stop()

        assert [cli.alive for cli in claude.sessions] == [False, False]
