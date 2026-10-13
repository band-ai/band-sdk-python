"""Which failures keep the room's conversation and which start a new one."""

from __future__ import annotations

import pytest
from claude_agent_sdk import (
    CLIConnectionError,
)

from band.core.retention import ReleaseCause
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
)


class TestResumePolicy:
    @pytest.mark.parametrize(
        ("resume", "attempts", "kept"),
        [(True, ["sess-gone"], "sess-gone"), (False, ["sess-gone", None], None)],
        ids=["strict-wake", "bootstrap-fallback"],
    )
    async def test_only_a_strict_wake_keeps_an_unresumable_session(
        self,
        claude: FakeClaude,
        resume: bool,
        attempts: list[str | None],
        kept: str | None,
    ) -> None:
        """A deliberate wake reports the failure and keeps the conversation's
        identity; bootstrap and crash recovery start a new conversation."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-gone")
        claude.unresumable.add("sess-gone")

        if resume:
            with pytest.raises(CLIConnectionError):
                await session.acquire(1, resume)
        else:
            await session.acquire(1, resume)
        await session.release(ReleaseCause.FINAL)

        assert claude.resumed == attempts
        assert session.session_id == kept
        assert not any(cli.alive for cli in claude.sessions)

    async def test_a_startup_that_fails_twice_keeps_the_conversation(
        self,
        claude: FakeClaude,
    ) -> None:
        """The fresh-session fallback drops the conversation only once a new one
        runs, so a startup failure unrelated to the resume leaves the next
        message resuming it."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-1")
        claude.refuse_connect = True

        with pytest.raises(CLIConnectionError):
            await exchange(session, "hi")
        claude.refuse_connect = False
        claude.script(thinking("back"))
        await exchange(session, "hi")
        await manager.stop()

        assert claude.resumed == ["sess-1", None, "sess-1"]

    async def test_a_failed_resume_that_could_not_close_is_never_replaced(
        self,
        claude: FakeClaude,
    ) -> None:
        """The bootstrap fallback only starts a new CLI once the failed one is
        closed; otherwise the failed CLI stays held for recovery."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-gone")
        claude.unresumable.add("sess-gone")
        claude.refuse_close = True

        with pytest.raises(CLIConnectionError, match="exited during startup"):
            await exchange(session, "hi")
        [cli] = claude.sessions
        held = (manager.has_session("room-a"), session.session_id)
        claude.refuse_close = False
        await manager.cleanup_session("room-a")

        assert held == (True, "sess-gone")
        assert claude.resumed == ["sess-gone"]
        assert not cli.alive
