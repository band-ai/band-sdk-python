"""Refused native declines surface blockers, recover with a reply, and leave the room usable."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.integrations.omprpc.decline import (
    compose_decline_refusals,
    decline_backlog_refusal,
    decline_sole_tool_refusal,
)
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpContentKind,
    OmpMessage,
    OmpState,
)
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.omp.declinekit import (
    DeclineTools,
    abort_count,
    decline_refusal_text,
    declining_adapter,
    executed,
    host_outcomes,
)
from tests.adapters.omp.declinepeer import (
    DECLINE_CALL,
    DECLINE_RETRY,
    REPLY_CALL,
    REPLY_TEXT,
    DeclineScenario,
)
from tests.adapters.omp.turns import run_turn


def busy(*, work: bool = False, queued: int = 0) -> OmpState:
    return OmpState(
        sessionId="session",
        isSettled=False,
        hasPendingAsyncWork=work,
        queuedMessageCount=queued,
    )


def tools(*call_ids: str) -> OmpMessage:
    return OmpMessage(
        role=OMP_ASSISTANT_ROLE,
        content=[
            OmpContent(
                type=OmpContentKind.TOOL_CALL,
                id=call_id,
                name=BandTool.NO_REPLY,
                arguments={},
            )
            for call_id in call_ids
        ],
    )


PENDING_WITH_YIELD = decline_backlog_refusal(busy(work=True))
QUEUED_WITH_YIELD = decline_backlog_refusal(busy(queued=1))
SOLE_SIBLING = decline_sole_tool_refusal(
    tools(DECLINE_CALL, "background"), DECLINE_CALL
)
BACKLOG_AND_SOLE = compose_decline_refusals(
    decline_backlog_refusal(busy(work=True), allow_yield=False),
    SOLE_SIBLING,
)


class TestDeclineRefusalRecovery:
    @pytest.mark.parametrize(
        ("scenario", "refusal"),
        [
            (DeclineScenario.BACKGROUND_PENDING, PENDING_WITH_YIELD),
            (DeclineScenario.BACKGROUND_QUEUED, QUEUED_WITH_YIELD),
            (DeclineScenario.MIXED_BATCH, SOLE_SIBLING),
            (DeclineScenario.BACKGROUND_MIXED, BACKLOG_AND_SOLE),
            (DeclineScenario.POST_CHECK_PENDING, PENDING_WITH_YIELD),
            (DeclineScenario.POST_CHECK_QUEUED, QUEUED_WITH_YIELD),
        ],
    )
    async def test_refused_decline_delivers_composed_blocker_then_reply(
        self,
        tmp_path: Path,
        scenario: DeclineScenario,
        refusal: str,
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, scenario) as adapter:
            await run_turn(adapter, tools)
            assert abort_count(tmp_path) == 0
            assert decline_refusal_text(tmp_path) == refusal

        assert host_outcomes(tmp_path) == {
            DECLINE_CALL: True,
            REPLY_CALL: False,
        }
        assert BandTool.SEND_MESSAGE in executed(tools)
        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_refused_decline_then_later_turn_can_decline(
        self, tmp_path: Path
    ) -> None:
        refused = DeclineTools()
        later = DeclineTools()
        async with declining_adapter(
            tmp_path, DeclineScenario.RECOVER_THEN_DECLINE
        ) as adapter:
            await run_turn(adapter, refused)
            assert abort_count(tmp_path) == 0
            assert decline_refusal_text(tmp_path) == PENDING_WITH_YIELD
            assert refused.chat == [REPLY_TEXT]

            await run_turn(adapter, later, bootstrap=False)
            assert abort_count(tmp_path) == 1
            assert host_outcomes(tmp_path)[DECLINE_RETRY] is False
            assert executed(later) == [BandTool.NO_REPLY]
            assert later.chat == []
            assert failure_reports(later) == []
