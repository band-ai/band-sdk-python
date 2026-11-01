"""A claimed manual answer retains permission through confirmation HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus

import pytest

from band.adapters.opencode.approvals.room import RoomApprovals
from band.adapters.opencode.approvals.wording import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    QUESTION_ANSWERED_TEMPLATE,
    QUESTION_REJECTED_TEMPLATE,
    QUESTION_TIMED_OUT_TEMPLATE,
)
from band.client.streaming import ControlMode
from band.integrations.opencode import (
    OpencodePermissionRequest,
    OpencodeQuestionRequest,
)
from tests.activitysupport import IDLE_BODY, PERMISSION_BODY, REASONING_BODY
from tests.adapters.opencode.activity.kit import publish_events
from tests.adapters.opencode.activity.notices import (
    ORIGIN_CAP_SECONDS,
    ORIGIN_MESSAGE_ID,
    OpenNoticeRoom,
)
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    AskFactory,
    model_answer,
    turn_finished,
)
from tests.runtime.helpers import ALICE

REQUEST_ID = "manual-answer"
APPROVAL = f"approve {REQUEST_ID}"
CONFIRMATION = APPROVAL_HANDLED_TEMPLATE.format(request_id=REQUEST_ID, reply="once")

manual_answers = pytest.mark.parametrize(
    ("ask", "provider_request", "answer", "confirmation"),
    [
        pytest.param(
            RoomApprovals.on_permission_asked,
            AskFactory().permission(REQUEST_ID),
            APPROVAL,
            CONFIRMATION,
            id="permission-approval",
        ),
        pytest.param(
            RoomApprovals.on_permission_asked,
            AskFactory().permission(REQUEST_ID),
            f"reject {REQUEST_ID}",
            APPROVAL_HANDLED_TEMPLATE.format(request_id=REQUEST_ID, reply="reject"),
            id="permission-rejection",
        ),
        pytest.param(
            RoomApprovals.on_question_asked,
            AskFactory().question(REQUEST_ID),
            "blue",
            QUESTION_ANSWERED_TEMPLATE.format(request_id=REQUEST_ID),
            id="question-answer",
        ),
        pytest.param(
            RoomApprovals.on_question_asked,
            AskFactory().question(REQUEST_ID),
            f"reject {REQUEST_ID}",
            QUESTION_REJECTED_TEMPLATE.format(request_id=REQUEST_ID),
            id="question-rejection",
        ),
    ],
)


class TestConfirmationActivity:
    @manual_answers
    async def test_confirmation_keeps_permission_until_its_http_completes(
        self,
        notice_room: OpenNoticeRoom,
        ask: Callable[..., Awaitable[None]],
        provider_request: OpencodePermissionRequest | OpencodeQuestionRequest,
        answer: str,
        confirmation: str,
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = confirmation
            await ask(room.approvals, provider_request, activity_tools=room.tools)
            assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(answer, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                assert not resolving.done()
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                room.platform.release.set()
                assert await resolving
                assert room.platform.room_messages[-1] == confirmation
                assert await room.platform.checkpoint(room.execution) == REASONING_BODY
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    @manual_answers
    async def test_failed_confirmation_releases_its_captured_manual_lease(
        self,
        notice_room: OpenNoticeRoom,
        ask: Callable[..., Awaitable[None]],
        provider_request: OpencodePermissionRequest | OpencodeQuestionRequest,
        answer: str,
        confirmation: str,
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = confirmation
            room.platform.status = HTTPStatus.BAD_REQUEST
            await ask(room.approvals, provider_request, activity_tools=room.tools)
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(answer, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                room.platform.release.set()
                assert await resolving
                assert confirmation not in room.platform.room_messages
                assert await room.platform.checkpoint(room.execution) == REASONING_BODY
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    @manual_answers
    async def test_cancelled_confirmation_releases_its_captured_manual_lease(
        self,
        notice_room: OpenNoticeRoom,
        ask: Callable[..., Awaitable[None]],
        provider_request: OpencodePermissionRequest | OpencodeQuestionRequest,
        answer: str,
        confirmation: str,
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = confirmation
            await ask(room.approvals, provider_request, activity_tools=room.tools)
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(answer, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                resolving.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await resolving
                assert confirmation not in room.platform.room_messages
                assert await room.platform.checkpoint(room.execution) == REASONING_BODY
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    async def test_settled_provider_keeps_the_claimed_confirmation_owned(
        self, notice_room: OpenNoticeRoom
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = CONFIRMATION
            await room.approvals.on_permission_asked(
                AskFactory().permission(REQUEST_ID), activity_tools=room.tools
            )
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(APPROVAL, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                await publish_events(
                    room.client,
                    *model_answer("completed-message", "Provider finished."),
                )
                await turn_finished(room.turn)
                await room.delivery
                await room.execution._join_activity_completion()
                assert not resolving.done()
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                room.platform.release.set()
                assert await resolving
                assert room.platform.room_messages[-1] == CONFIRMATION
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    async def test_room_stop_cancels_the_claimed_notice_and_clears_permission(
        self, notice_room: OpenNoticeRoom
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = CONFIRMATION
            await room.approvals.on_permission_asked(
                AskFactory().permission(REQUEST_ID), activity_tools=room.tools
            )
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(APPROVAL, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                await publish_events(
                    room.client,
                    *model_answer("completed-message", "Provider finished."),
                )
                await turn_finished(room.turn)
                await room.delivery
                assert room.execution.interrupt(kind=ControlMode.STOP)
                with pytest.raises(asyncio.CancelledError):
                    await resolving
                await room.execution._join_activity_completion()
                assert room.platform.activity_bodies[-1] == IDLE_BODY
                assert CONFIRMATION not in room.platform.room_messages
                assert room.platform.marked("processed") == [ORIGIN_MESSAGE_ID]
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    @pytest.mark.looptime
    async def test_claimed_answer_keeps_the_original_cap_and_settled_delivery(
        self, notice_room: OpenNoticeRoom
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = CONFIRMATION
            await room.approvals.on_permission_asked(
                AskFactory().permission(REQUEST_ID), activity_tools=room.tools
            )
            await room.delivery
            await asyncio.sleep(ORIGIN_CAP_SECONDS - 1)
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(APPROVAL, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                await publish_events(
                    room.client,
                    *model_answer("completed-message", "Provider finished."),
                )
                await turn_finished(room.turn)
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                await asyncio.sleep(2)
                reports = list(room.platform.activity_bodies)
                assert not await room.execution._report_working_state(True)
                assert room.platform.activity_bodies == reports
                assert not resolving.done()
                assert room.platform.marked("processed") == [ORIGIN_MESSAGE_ID]
                room.platform.release.set()
                assert await resolving
                await room.execution._join_activity_completion()
                assert room.platform.activity_bodies[-1] == IDLE_BODY
                assert room.platform.marked("processed") == [ORIGIN_MESSAGE_ID]
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    async def test_another_answer_cannot_release_the_claimants_notice_lease(
        self, notice_room: OpenNoticeRoom
    ) -> None:
        async with notice_room() as room:
            room.platform.notice = CONFIRMATION
            await room.approvals.on_permission_asked(
                AskFactory().permission(REQUEST_ID), activity_tools=room.tools
            )
            resolving = asyncio.create_task(
                room.approvals.try_handle_reply(APPROVAL, ALICE["id"])
            )
            try:
                await room.platform.entered.wait()
                assert await room.approvals.try_handle_reply(APPROVAL, ALICE["id"])
                room.approvals.cancel()
                assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
                room.platform.release.set()
                assert await resolving
                assert await room.platform.checkpoint(room.execution) == REASONING_BODY
                assert room.client.permission_replies == [
                    {"permission_id": REQUEST_ID, "reply": "once"}
                ]
            finally:
                room.platform.release.set()
                await asyncio.gather(resolving, return_exceptions=True)

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        ("ask", "provider_request", "confirmation"),
        [
            (
                RoomApprovals.on_permission_asked,
                AskFactory().permission(REQUEST_ID),
                APPROVAL_TIMED_OUT_TEMPLATE.format(
                    request_id=REQUEST_ID, reply="reject"
                ),
            ),
            (
                RoomApprovals.on_question_asked,
                AskFactory().question(REQUEST_ID),
                QUESTION_TIMED_OUT_TEMPLATE.format(request_id=REQUEST_ID),
            ),
        ],
        ids=["permission", "question"],
    )
    async def test_expiry_owns_the_notice_after_provider_completion(
        self,
        notice_room: OpenNoticeRoom,
        ask: Callable[..., Awaitable[None]],
        provider_request: OpencodePermissionRequest | OpencodeQuestionRequest,
        confirmation: str,
    ) -> None:
        async with notice_room(reporting_cap=None) as room:
            room.platform.notice = confirmation
            await ask(room.approvals, provider_request, activity_tools=room.tools)
            await room.delivery
            assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
            await asyncio.sleep(ASK_DEADLINE_S)
            await room.platform.entered.wait()
            await publish_events(
                room.client, *model_answer("completed-message", "Provider finished.")
            )
            await turn_finished(room.turn)
            assert await room.platform.checkpoint(room.execution) == PERMISSION_BODY
            room.platform.release.set()
            assert await room.platform.body_matching(IDLE_BODY) == IDLE_BODY
            assert room.platform.confirmed_notices == [confirmation]
