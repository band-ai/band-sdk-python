"""The release callback refuses idle release and keeps a CLI it failed to close."""

from __future__ import annotations

import pytest
from claude_agent_sdk import CLIConnectionError

from band.core.retention import ReleaseCause, ReleaseOutcome
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
    thoughts,
)


class TestReleaseCallbacks:
    async def test_failed_startup_cleanup_is_retried_on_the_same_process(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        session = manager.room("room-a")
        claude.refuse_connect = claude.refuse_close = True

        with pytest.raises(CLIConnectionError, match="exited during startup"):
            await exchange(session, "hi")
        claude.refuse_connect = False
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await exchange(session, "hi")
        assert manager.has_session("room-a")
        assert len(claude.sessions) == 1

        claude.refuse_close = False
        claude.script(thinking("started"))
        assert thoughts(await exchange(session, "hi")) == ["started"]
        await manager.stop()

        assert [cli.alive for cli in claude.sessions] == [False, False]

    async def test_an_idle_release_is_refused_with_the_cli_intact(
        self,
        claude: FakeClaude,
    ) -> None:
        """Claude idle release is unqualified, so the callback refuses before any
        teardown: the next turn reads its answer through the same CLI, reader and
        conversation. The callback is called directly because the adapter never
        enables idle retention."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-1")
        claude.script(thinking("one"), thinking("two"))
        await exchange(session, "one")

        outcome = await session.release(ReleaseCause.IDLE)
        second = await exchange(session, "two")
        await manager.stop()

        assert outcome is ReleaseOutcome.BUSY
        assert thoughts(second) == ["two"]
        assert claude.resumed == ["sess-1"]

    @pytest.mark.parametrize("cause", [ReleaseCause.RECOVERY, ReleaseCause.FINAL])
    async def test_recovery_and_final_releases_close_or_keep_the_cli_for_a_retry(
        self, claude: FakeClaude, cause: ReleaseCause
    ) -> None:
        manager = new_manager()
        session = manager.room("room-a")
        await session.acquire(1, False)
        claude.refuse_close = True

        with pytest.raises(RuntimeError, match="cleanup failed"):
            await session.release(cause)
        held = session.holds_cli
        claude.refuse_close = False
        outcome = await session.release(cause)
        await manager.stop()

        assert (held, outcome, session.holds_cli) == (
            True,
            ReleaseOutcome.RELEASED,
            False,
        )
        assert not any(cli.alive for cli in claude.sessions)
