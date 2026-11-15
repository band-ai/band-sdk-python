"""Successful host declines stop native continuation and retain room usability."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.integrations.omprpc.errors import OmpPromptError
from band.integrations.ompwire import OmpPromptStatus
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.hold import Hold
from tests.adapters.omp.declinekit import (
    CustomDeclineInput,
    DeclineTools,
    abort_count,
    admitted_declines,
    custom_decline,
    decline_refused,
    declining_adapter,
    executed,
    host_outcomes,
)
from tests.adapters.omp.declinepeer import (
    ADMIT_SECOND_DECLINE,
    DECLINE_CALL,
    REPLY_CALL,
    REPLY_TEXT,
    SECOND_DECLINE_CALL,
    DeclineScenario,
)
from tests.adapters.omp.mailboxkit import AdmissionMailbox, room_client
from tests.adapters.omp.turns import run_turn

REFUSED_THEN_REPLY = {
    DECLINE_CALL: True,
    REPLY_CALL: False,
}


class TestTerminalDecline:
    async def test_queued_declines_share_one_owned_stop(self, tmp_path: Path) -> None:
        hold = Hold()
        tools = DeclineTools(decline_hold=hold)
        async with declining_adapter(tmp_path, DeclineScenario.CONCURRENT) as adapter:
            mailbox = AdmissionMailbox()
            adapter._mailbox = mailbox
            turn = asyncio.create_task(run_turn(adapter, tools))
            try:
                await hold.arrived()
                mailbox.discard_admissions()
                await room_client(adapter).request(ADMIT_SECOND_DECLINE)
                assert await mailbox.admitted.get() == "room-123"
                assert executed(tools) == []
                assert abort_count(tmp_path) == 0
                hold.released.set()
                await turn
            finally:
                hold.released.set()
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

            assert executed(tools) == [BandTool.NO_REPLY]
            assert abort_count(tmp_path) == 1
            assert host_outcomes(tmp_path) == {DECLINE_CALL: False}
            assert failure_reports(tools) == []
            assert admitted_declines(tmp_path) == {
                DECLINE_CALL,
                SECOND_DECLINE_CALL,
            }

    async def test_successful_decline_stops_later_calls_and_allows_next_turn(
        self, tmp_path: Path
    ) -> None:
        declined = DeclineTools()
        follow_up = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.SUCCESS) as adapter:
            await run_turn(adapter, declined)

            assert declined.chat == []
            assert failure_reports(declined) == []
            assert executed(declined) == [BandTool.NO_REPLY]
            assert host_outcomes(tmp_path) == {DECLINE_CALL: False}

            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [REPLY_TEXT]
            assert failure_reports(follow_up) == []

    @pytest.mark.parametrize(
        "scenario",
        [
            DeclineScenario.BACKGROUND_PENDING,
            DeclineScenario.BACKGROUND_QUEUED,
            DeclineScenario.BACKGROUND_COMPLETING,
            DeclineScenario.BACKGROUND_BOTH,
            DeclineScenario.BACKGROUND_MIXED,
            DeclineScenario.MIXED_BATCH,
            DeclineScenario.MISSING_BATCH,
            DeclineScenario.STRING_CONTENT,
            DeclineScenario.MISMATCHED_ID,
        ],
    )
    async def test_background_work_refuses_decline_and_keeps_reply_available(
        self,
        tmp_path: Path,
        scenario: DeclineScenario,
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, scenario) as adapter:
            await run_turn(adapter, tools)
            assert abort_count(tmp_path) == 0
            assert host_outcomes(tmp_path) == REFUSED_THEN_REPLY

        assert decline_refused(tmp_path)
        assert executed(tools) == [BandTool.SEND_MESSAGE]
        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    @pytest.mark.parametrize(
        "scenario",
        [
            DeclineScenario.POST_CHECK_PENDING,
            DeclineScenario.POST_CHECK_QUEUED,
        ],
    )
    async def test_post_check_pending_work_refuses_after_execute(
        self,
        tmp_path: Path,
        scenario: DeclineScenario,
    ) -> None:
        """Pending work that appears after the initial check must still block ABORT."""
        tools = DeclineTools()
        async with declining_adapter(tmp_path, scenario) as adapter:
            await run_turn(adapter, tools)
            assert abort_count(tmp_path) == 0
            assert host_outcomes(tmp_path) == REFUSED_THEN_REPLY

        assert decline_refused(tmp_path)
        assert executed(tools) == [BandTool.NO_REPLY, BandTool.SEND_MESSAGE]
        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_cancel_after_abort_write_does_not_record_decline(
        self, tmp_path: Path
    ) -> None:
        """HOST_TOOL_CANCEL after ABORT write must not leave an unpaired DECLINE."""
        tools = DeclineTools()
        async with declining_adapter(
            tmp_path, DeclineScenario.CANCEL_AFTER_ABORT
        ) as adapter:
            with pytest.raises(asyncio.CancelledError):
                await run_turn(adapter, tools)
            assert tools.chat == []
            assert abort_count(tmp_path) >= 1
            assert failure_reports(tools) == []

    async def test_cancelled_snapshot_cannot_record_a_decline(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools()
        follow_up = DeclineTools()
        async with declining_adapter(
            tmp_path, DeclineScenario.CANCEL_SNAPSHOT
        ) as adapter:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert executed(tools) == []
            assert abort_count(tmp_path) == 0
            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [REPLY_TEXT]
            assert failure_reports(follow_up) == []

    async def test_failed_decline_can_recover_with_a_reply(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools(fail_decline=True)
        async with declining_adapter(tmp_path, DeclineScenario.FAILED) as adapter:
            await run_turn(adapter, tools)

        assert decline_refused(tmp_path)
        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_ordinary_tools_continue_to_a_reply(self, tmp_path: Path) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.ORDINARY) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_custom_tool_named_no_reply_can_continue(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(
            tmp_path,
            DeclineScenario.SUCCESS,
            additional_tools=[(CustomDeclineInput, custom_decline)],
            exclude_tools=(BandTool.NO_REPLY,),
        ) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []

    async def test_unrequested_abort_still_fails(self, tmp_path: Path) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, DeclineScenario.ABORTED) as adapter:
            with pytest.raises(OmpPromptError):
                await run_turn(adapter, tools)

        assert tools.chat == []
        assert failure_reports(tools)

    async def test_provider_error_is_not_hidden_by_intentional_stop(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(
            tmp_path, DeclineScenario.PROVIDER_ERROR
        ) as adapter:
            with pytest.raises(OmpPromptError) as failure:
                await run_turn(adapter, tools)

        assert failure.value.status == OmpPromptStatus.ERROR
        assert tools.chat == []
