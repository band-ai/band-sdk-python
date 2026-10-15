"""the runtime reports a turn only when its report did not post behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.testing import MISSING_REPLY_FAILURE
from tests.runtime.helpers import (
    deliver,
    failure_posts,
    run_through,
)
from tests.runtime.verdict.kit import (
    ADAPTER_FAILURE,
    GENERIC_FAILURE,
    ScriptedAdapter,
    Step,
    report_and_stop,
)


class TestTheRuntimeReportsATurnOnlyWhenItsReportDidNotPost:
    @pytest.mark.parametrize("path", ["live", "backlog"])
    @pytest.mark.parametrize(
        "report_posts", [True, False], ids=["posted", "post-failed"]
    )
    @pytest.mark.parametrize(
        ("steps", "report"),
        [
            pytest.param([], MISSING_REPLY_FAILURE, id="missing-reply"),
            pytest.param([report_and_stop], ADAPTER_FAILURE, id="adapter-report"),
        ],
    )
    async def test_the_runtime_reports_a_turn_only_when_its_report_did_not_post(
        self,
        link: MagicMock,
        path: str,
        steps: list[Step],
        report: tuple[str, str],
        report_posts: bool,
    ) -> None:
        """Raising ``TurnResultAlreadyReported`` is not enough: the runtime's
        fallback runs unless the turn's own report actually reached the room."""
        if not report_posts:
            create = link.rest.agent_api_events.create_agent_chat_event
            create.side_effect = [RuntimeError("transient 503"), create.return_value]
        ctx = run_through(ScriptedAdapter(steps), link)

        await deliver(ctx, path)

        link.mark_failed.assert_awaited_once()
        fallback = [] if report_posts else [GENERIC_FAILURE]
        assert failure_posts(link) == [report, *fallback]
