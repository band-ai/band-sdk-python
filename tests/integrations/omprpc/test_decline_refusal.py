"""Decline refusal text matches each observed backlog and sole-tool branch."""

from __future__ import annotations

from band.integrations.omprpc.decline import (
    NATIVE_BACKLOG_YIELD_GUIDANCE,
    NATIVE_QUEUED_INPUT_GUIDANCE,
    NATIVE_REFUSED_DECLINE_GUIDANCE,
    PENDING_ASYNC_WORK_LABEL,
    RECONSIDER_PENDING_INPUT,
    RECONSIDER_PENDING_WORK,
    RECONSIDER_PENDING_WORK_AND_INPUT,
    SOLE_TOOL_ID_MISMATCH_RECOVERY,
    SOLE_TOOL_SIBLING_RECOVERY,
    SOLE_TOOL_UNSTRUCTURED_RECOVERY,
    compose_decline_refusals,
    decline_backlog_refusal,
    decline_sole_tool_refusal,
    queued_message_count_label,
    tool_call_id_label,
)
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpContentKind,
    OmpMessage,
    OmpState,
)
from band.runtime.tools import BandTool

DECLINE_ID = "decline"
DECLINE_LABEL = tool_call_id_label(DECLINE_ID)


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


class TestDeclineRefusal:
    def test_idle_snapshot_allows_decline(self) -> None:
        assert decline_backlog_refusal(busy()) is None
        assert decline_sole_tool_refusal(tools(DECLINE_ID), DECLINE_ID) is None
        assert compose_decline_refusals(None, None) is None

    def test_pending_async_work_refuses_with_yield(self) -> None:
        assert decline_backlog_refusal(busy(work=True)) == (
            f"{BandTool.NO_REPLY} refused: native {PENDING_ASYNC_WORK_LABEL}. "
            "Wait for the existing async work and handle its completion. "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {NATIVE_BACKLOG_YIELD_GUIDANCE} "
            f"{RECONSIDER_PENDING_WORK}"
        )

    def test_queued_messages_refuse_with_yield(self) -> None:
        assert decline_backlog_refusal(busy(queued=1)) == (
            f"{BandTool.NO_REPLY} refused: native {queued_message_count_label(1)}. "
            "Allow OMP to deliver the queued messages on continuation, then read and "
            f"handle them. {NATIVE_QUEUED_INPUT_GUIDANCE} "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {NATIVE_BACKLOG_YIELD_GUIDANCE} "
            f"{RECONSIDER_PENDING_INPUT}"
        )

    def test_queued_message_count_uses_observed_count(self) -> None:
        assert decline_backlog_refusal(busy(queued=3)) == (
            f"{BandTool.NO_REPLY} refused: native {queued_message_count_label(3)}. "
            "Allow OMP to deliver the queued messages on continuation, then read and "
            f"handle them. {NATIVE_QUEUED_INPUT_GUIDANCE} "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {NATIVE_BACKLOG_YIELD_GUIDANCE} "
            f"{RECONSIDER_PENDING_INPUT}"
        )

    def test_work_and_input_refuse_together(self) -> None:
        assert decline_backlog_refusal(busy(work=True, queued=1)) == (
            f"{BandTool.NO_REPLY} refused: native {PENDING_ASYNC_WORK_LABEL} "
            f"{queued_message_count_label(1)}. "
            "Wait for the existing async work and handle its completion. "
            "Allow OMP to deliver the queued messages on continuation, then read and "
            f"handle them. {NATIVE_QUEUED_INPUT_GUIDANCE} "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {NATIVE_BACKLOG_YIELD_GUIDANCE} "
            f"{RECONSIDER_PENDING_WORK_AND_INPUT}"
        )

    def test_backlog_omits_yield_when_disallowed(self) -> None:
        assert decline_backlog_refusal(busy(work=True), allow_yield=False) == (
            f"{BandTool.NO_REPLY} refused: native {PENDING_ASYNC_WORK_LABEL}. "
            "Wait for the existing async work and handle its completion. "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {RECONSIDER_PENDING_WORK}"
        )

    def test_sibling_tool_calls_refuse_sole_proof(self) -> None:
        assert decline_sole_tool_refusal(
            tools(DECLINE_ID, "background"), DECLINE_ID
        ) == (
            f"{BandTool.NO_REPLY} refused: no sole-tool-call proof for {DECLINE_LABEL} "
            "(2 TOOL_CALL blocks in the assistant message). "
            f"{SOLE_TOOL_SIBLING_RECOVERY} {NATIVE_REFUSED_DECLINE_GUIDANCE}"
        )

    def test_unstructured_content_refuses_sole_proof(self) -> None:
        message = OmpMessage(role=OMP_ASSISTANT_ROLE, content="plain text")
        assert decline_sole_tool_refusal(message, DECLINE_ID) == (
            f"{BandTool.NO_REPLY} refused: no sole-tool-call proof for {DECLINE_LABEL} "
            "(string or missing content). "
            f"{SOLE_TOOL_UNSTRUCTURED_RECOVERY} {NATIVE_REFUSED_DECLINE_GUIDANCE}"
        )

    def test_empty_tool_blocks_refuse_sole_proof(self) -> None:
        message = OmpMessage(role=OMP_ASSISTANT_ROLE, content=[])
        assert decline_sole_tool_refusal(message, DECLINE_ID) == (
            f"{BandTool.NO_REPLY} refused: no sole-tool-call proof for {DECLINE_LABEL} "
            "(no TOOL_CALL blocks). "
            f"{SOLE_TOOL_UNSTRUCTURED_RECOVERY} {NATIVE_REFUSED_DECLINE_GUIDANCE}"
        )

    def test_mismatched_tool_id_refuses_sole_proof(self) -> None:
        assert decline_sole_tool_refusal(tools("other-id"), DECLINE_ID) == (
            f"{BandTool.NO_REPLY} refused: no sole-tool-call proof for {DECLINE_LABEL} "
            f"(assistant {tool_call_id_label('other-id')}). "
            f"{SOLE_TOOL_ID_MISMATCH_RECOVERY} {NATIVE_REFUSED_DECLINE_GUIDANCE}"
        )

    def test_compose_reports_backlog_and_sole_without_yield(self) -> None:
        assert compose_decline_refusals(
            decline_backlog_refusal(busy(work=True), allow_yield=False),
            decline_sole_tool_refusal(tools(DECLINE_ID, "background"), DECLINE_ID),
        ) == (
            f"{BandTool.NO_REPLY} refused: native {PENDING_ASYNC_WORK_LABEL}. "
            "Wait for the existing async work and handle its completion. "
            f"{NATIVE_REFUSED_DECLINE_GUIDANCE} {RECONSIDER_PENDING_WORK} "
            f"{BandTool.NO_REPLY} refused: no sole-tool-call proof for {DECLINE_LABEL} "
            "(2 TOOL_CALL blocks in the assistant message). "
            f"{SOLE_TOOL_SIBLING_RECOVERY} {NATIVE_REFUSED_DECLINE_GUIDANCE}"
        )
