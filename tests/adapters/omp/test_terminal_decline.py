"""Successful host declines stop native continuation and retain room usability."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.integrations.omprpc.decline import (
    compose_decline_refusals,
    decline_backlog_refusal,
    decline_sole_tool_refusal,
)
from band.integrations.omprpc.errors import OmpPromptError
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpContentKind,
    OmpMessage,
    OmpPromptStatus,
    OmpState,
)
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.hold import Hold
from tests.adapters.omp.declinekit import (
    CustomDeclineInput,
    DeclineTools,
    custom_decline,
    decline_ids_before_stop,
    declining_adapter,
    stop_commands,
    tool_result_errors,
    tool_result_text,
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


def busy_state(*, work: bool = False, queued: int = 0) -> OmpState:
    return OmpState(
        sessionId="session",
        isSettled=False,
        hasPendingAsyncWork=work,
        queuedMessageCount=queued,
    )


def tool_message(*call_ids: str) -> OmpMessage:
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


def string_message() -> OmpMessage:
    return OmpMessage(role=OMP_ASSISTANT_ROLE, content=f"{BandTool.NO_REPLY}({{}})")


def missing_message() -> OmpMessage | None:
    return None


PENDING_REFUSAL = decline_backlog_refusal(busy_state(work=True))
QUEUED_REFUSAL = decline_backlog_refusal(busy_state(queued=1))
BOTH_REFUSAL = decline_backlog_refusal(busy_state(work=True, queued=1))
MIXED_BATCH_REFUSAL = decline_sole_tool_refusal(
    tool_message(DECLINE_CALL, "background"), DECLINE_CALL
)
MISSING_REFUSAL = decline_sole_tool_refusal(missing_message(), DECLINE_CALL)
STRING_REFUSAL = decline_sole_tool_refusal(string_message(), DECLINE_CALL)
MISMATCHED_REFUSAL = decline_sole_tool_refusal(tool_message("other-id"), DECLINE_CALL)
BACKGROUND_MIXED_REFUSAL = compose_decline_refusals(
    decline_backlog_refusal(busy_state(work=True), allow_yield=False),
    decline_sole_tool_refusal(tool_message(DECLINE_CALL, "background"), DECLINE_CALL),
)


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
                assert tools.tool_calls == []
                assert stop_commands(tmp_path) == []
                hold.released.set()
                await turn
            finally:
                hold.released.set()
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

            assert {call["tool_name"] for call in tools.tool_calls} == {
                BandTool.NO_REPLY
            }
            assert len(stop_commands(tmp_path)) == 1
            assert tool_result_errors(tmp_path) == {DECLINE_CALL: False}
            assert failure_reports(tools) == []
            assert decline_ids_before_stop(tmp_path) == {
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
            assert {call["tool_name"] for call in declined.tool_calls} == {
                BandTool.NO_REPLY
            }
            assert tool_result_errors(tmp_path) == {DECLINE_CALL: False}

            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [REPLY_TEXT]
            assert failure_reports(follow_up) == []

    @pytest.mark.parametrize(
        ("scenario", "refusal"),
        [
            (DeclineScenario.BACKGROUND_PENDING, PENDING_REFUSAL),
            (DeclineScenario.BACKGROUND_QUEUED, QUEUED_REFUSAL),
            (DeclineScenario.BACKGROUND_COMPLETING, PENDING_REFUSAL),
            (DeclineScenario.BACKGROUND_BOTH, BOTH_REFUSAL),
            (DeclineScenario.BACKGROUND_MIXED, BACKGROUND_MIXED_REFUSAL),
            (DeclineScenario.MIXED_BATCH, MIXED_BATCH_REFUSAL),
            (DeclineScenario.MISSING_BATCH, MISSING_REFUSAL),
            (DeclineScenario.STRING_CONTENT, STRING_REFUSAL),
            (DeclineScenario.MISMATCHED_ID, MISMATCHED_REFUSAL),
        ],
    )
    async def test_background_work_refuses_decline_and_keeps_reply_available(
        self,
        tmp_path: Path,
        scenario: DeclineScenario,
        refusal: str,
    ) -> None:
        tools = DeclineTools()
        async with declining_adapter(tmp_path, scenario) as adapter:
            await run_turn(adapter, tools)
            assert stop_commands(tmp_path) == []

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []
        assert {call["tool_name"] for call in tools.tool_calls} == {
            BandTool.SEND_MESSAGE
        }
        assert tool_result_errors(tmp_path) == {
            DECLINE_CALL: True,
            REPLY_CALL: False,
        }
        assert tool_result_text(tmp_path, DECLINE_CALL) == refusal

    @pytest.mark.parametrize(
        ("scenario", "refusal"),
        [
            (DeclineScenario.POST_CHECK_PENDING, PENDING_REFUSAL),
            (DeclineScenario.POST_CHECK_QUEUED, QUEUED_REFUSAL),
        ],
    )
    async def test_post_check_pending_work_refuses_after_execute(
        self,
        tmp_path: Path,
        scenario: DeclineScenario,
        refusal: str,
    ) -> None:
        """Pending work that appears after the initial check must still block ABORT."""
        tools = DeclineTools()
        async with declining_adapter(tmp_path, scenario) as adapter:
            await run_turn(adapter, tools)
            assert stop_commands(tmp_path) == []
            assert tool_result_text(tmp_path, DECLINE_CALL) == refusal

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []
        assert [call["tool_name"] for call in tools.tool_calls] == [
            BandTool.NO_REPLY,
            BandTool.SEND_MESSAGE,
        ]
        assert tool_result_errors(tmp_path) == {
            DECLINE_CALL: True,
            REPLY_CALL: False,
        }
        assert tools.turn.replied is True

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
            assert tools.turn.replied is False
            assert stop_commands(tmp_path)
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
            assert tools.tool_calls == []
            assert stop_commands(tmp_path) == []
            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [REPLY_TEXT]
            assert failure_reports(follow_up) == []

    async def test_failed_decline_can_recover_with_a_reply(
        self, tmp_path: Path
    ) -> None:
        tools = DeclineTools(fail_decline=True)
        async with declining_adapter(tmp_path, DeclineScenario.FAILED) as adapter:
            await run_turn(adapter, tools)

        assert tools.chat == [REPLY_TEXT]
        assert failure_reports(tools) == []
        assert tool_result_errors(tmp_path)[DECLINE_CALL] is True

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
