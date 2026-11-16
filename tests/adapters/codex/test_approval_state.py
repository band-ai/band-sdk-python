"""Codex adapter: each room's approval state stays bounded and room-scoped."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from band.adapters.codex.approvals import CodexApprovals, PendingApproval
from band.integrations.codex.types import CodexApprovalMethod
from tests.adapters.codex.kit import ROOM_ID

OTHER_ROOM = "room-other"


def approvals(*, max_session: int = 3, max_audit: int = 5) -> CodexApprovals:
    return CodexApprovals(
        max_pending=2,
        max_session_approved=max_session,
        max_audit=max_audit,
        granularity="full_command",
    )


def pending_request(request_id: int, native_key: str) -> PendingApproval:
    return PendingApproval(
        request_id=request_id,
        method=CodexApprovalMethod.COMMAND_EXECUTION,
        summary="command: ls",
        created_at=datetime.now(UTC),
        future=asyncio.get_running_loop().create_future(),
        native_key=native_key,
    )


def record_accept(state: CodexApprovals, request_id: str) -> None:
    state.record(
        ROOM_ID,
        request_id=request_id,
        method=CodexApprovalMethod.COMMAND_EXECUTION,
        decision="accept",
        decided_by="tester",
    )


class TestCodexApprovals:
    def test_audit_keeps_the_most_recent_decisions(self) -> None:
        state = approvals(max_audit=5)
        for i in range(10):
            record_accept(state, str(i))
        assert [entry.request_id for entry in state.audit(ROOM_ID)] == [
            "5",
            "6",
            "7",
            "8",
            "9",
        ]
        assert state.audit(OTHER_ROOM) == []

    def test_session_approvals_evict_the_least_recently_approved(self) -> None:
        state = approvals(max_session=3)
        for key in ("a", "b", "c", "a", "d"):
            state.approve_for_session(ROOM_ID, f"commandExecution:{key}")
        assert not state.is_session_approved(ROOM_ID, "commandExecution:b")
        assert state.session_patterns(ROOM_ID) == [
            "commandExecution:a",
            "commandExecution:c",
            "commandExecution:d",
        ]
        assert not state.is_session_approved(OTHER_ROOM, "commandExecution:a")

    def test_an_empty_key_is_never_session_approved(self) -> None:
        state = approvals()
        state.approve_for_session(ROOM_ID, "")
        assert not state.is_session_approved(ROOM_ID, "")

    @pytest.mark.asyncio
    async def test_a_redelivered_request_keeps_its_token(self) -> None:
        state = approvals()
        first = state.register(ROOM_ID, pending_request(1, "item-1"))
        again = state.register(ROOM_ID, pending_request(2, "item-1"))
        assert first is not None and again is not None
        assert again.entry.token == first.entry.token
        assert state.open_count(ROOM_ID) == 1

    @pytest.mark.asyncio
    async def test_declining_all_keeps_the_rooms_session_and_audit(self) -> None:
        state = approvals()
        request = pending_request(1, "item-1")
        state.register(ROOM_ID, request)
        state.approve_for_session(ROOM_ID, "commandExecution:npm")
        record_accept(state, "1")

        state.decline_all(ROOM_ID)

        assert request.future.result() == "decline"
        assert state.open_count(ROOM_ID) == 0
        assert state.session_patterns(ROOM_ID) == ["commandExecution:npm"]
        assert [entry.request_id for entry in state.audit(ROOM_ID)] == ["1"]

    @pytest.mark.asyncio
    async def test_forgetting_a_room_declines_its_requests_and_spends_tokens(
        self,
    ) -> None:
        state = approvals()
        request = pending_request(1, "item-1")
        first = state.register(ROOM_ID, request)
        state.approve_for_session(ROOM_ID, "commandExecution:npm")
        record_accept(state, "1")

        state.forget_room(ROOM_ID)

        assert request.future.result() == "decline"
        assert state.open_count(ROOM_ID) == 0
        assert state.session_patterns(ROOM_ID) == []
        assert state.audit(ROOM_ID) == []
        later = state.register(ROOM_ID, pending_request(2, "item-2"))
        assert first is not None and later is not None
        assert later.entry.token != first.entry.token
