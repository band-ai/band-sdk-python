"""Room disposal and manager stop keep unfinished or failed cleanup owned."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManagerStoppedError,
)
from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
)


class TestDisposal:
    async def test_an_unfinished_disposal_stays_owned_and_a_retry_joins_it(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        session = manager.room("room-a")
        claude.script(thinking("hi"))
        await exchange(session, "hi")
        closing = claude.closing = Hold()

        leaving = asyncio.create_task(manager.cleanup_session("room-a"))
        await closing.reached.wait()
        leaving.cancel()
        with pytest.raises(CleanupIncompleteError):
            await manager.cleanup_session("room-a")
        [cli] = claude.sessions
        assert (cli.close_calls, manager.has_session("room-a")) == (1, True)

        closing.released.set()
        await manager.cleanup_session("room-a")
        await manager.stop()

        assert not cli.alive
        assert manager.session("room-a") is None

    async def test_a_stopped_manager_keeps_failed_rooms_and_refuses_new_ones(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        claude.script(thinking("hi"))
        await exchange(manager.room("room-a"), "hi")
        claude.refuse_close = True

        with pytest.raises(ExceptionGroup) as failed:
            await manager.stop()
        assert failed.group_contains(RuntimeError, match="cleanup failed")
        with pytest.raises(ClaudeSessionManagerStoppedError):
            manager.room("room-b")
        claude.refuse_close = False
        await manager.stop()

        [cli] = claude.sessions
        assert not cli.alive
        assert manager.get_active_rooms() == []
