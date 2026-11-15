from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.sessionconfig import SessionConfig
from tests.runtime.helpers import (
    deliver,
    run_through,
    runtime_failures,
)
from tests.runtime.verdict.kit import (
    ScriptedAdapter,
)


class TestASessionThatReportsNoFailuresPostsNoMissingReply:
    async def test_a_session_that_reports_no_failures_posts_no_missing_reply(
        self,
        link: MagicMock,
    ) -> None:
        config = SessionConfig(
            enable_context_hydration=False, report_turn_failures_to_room=False
        )
        ctx = run_through(ScriptedAdapter([]), link, config=config)

        await deliver(ctx, "live")

        link.mark_failed.assert_awaited_once()
        assert runtime_failures(link) == []
