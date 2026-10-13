"""The release callback keeps a CLI it failed to close for a retry."""

from __future__ import annotations

import re

import pytest

from band.core.retention import ReleaseCause, ReleaseOutcome
from tests.adapters.claude_sdk.fakecli import CLEANUP_FAILED, FakeClaude
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
)


class TestReleaseCallbacks:
    @pytest.mark.parametrize("cause", [ReleaseCause.RECOVERY, ReleaseCause.FINAL])
    async def test_recovery_and_final_releases_close_or_keep_the_cli_for_a_retry(
        self, claude: FakeClaude, cause: ReleaseCause
    ) -> None:
        manager = new_manager()
        session = manager.room("room-a")
        await session.acquire(1, False)
        claude.refuse_close = True

        with pytest.raises(RuntimeError, match=re.escape(CLEANUP_FAILED)):
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
