"""band-trigger reports the platform's own error message, whether or not Fern types the status."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import AuthMode, run
from tests.cli.triggerwire.kit import (
    CHATS,
    INVALID_KEY,
    MESSAGES,
    ORPHAN_WARNING,
    PARTICIPANTS,
    PEERS,
    ROOM_ID,
    TARGET,
    added_participant,
    answer,
    answer_peer_pages,
    created_room,
    page_params,
    platform_error,
    trigger_args,
    validation_error,
)

ROOM_LIMIT = "Chat room limit reached."
INVALID_CHAT = "Validation failed"
UNKNOWN_PARTICIPANT = "Participant not found"
INVALID_PARTICIPANT = "Participant cannot join this room"
MESSAGE_FORBIDDEN = "Messaging this agent is not allowed"

# 403/404 parse into Fern's ``Error`` model; an undeclared or ``Any``-typed
# status such as 422 leaves the body a plain dict. A body without a usable
# ``error.message`` falls back to the status.
CHAT_REJECTIONS = [
    pytest.param(403, platform_error("forbidden", ROOM_LIMIT), ROOM_LIMIT, id="403"),
    pytest.param(
        422,
        validation_error(INVALID_CHAT, {"/chat/title": ["is invalid"]}),
        INVALID_CHAT,
        id="422",
    ),
    pytest.param(
        422, {"error": {"message": 42}}, "HTTP 422", id="422-non-string-message"
    ),
    pytest.param(
        422, {"errors": {"title": ["is invalid"]}}, "HTTP 422", id="422-no-envelope"
    ),
]
PARTICIPANT_REJECTIONS = [
    pytest.param(
        404,
        platform_error("not_found", UNKNOWN_PARTICIPANT),
        UNKNOWN_PARTICIPANT,
        id="404",
    ),
    pytest.param(
        422,
        validation_error(INVALID_PARTICIPANT, {"/participant_id": ["is invalid"]}),
        INVALID_PARTICIPANT,
        id="422",
    ),
]


class TestTriggerPlatformErrors:
    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_peer_lookup_rejection_reports_the_platform_message(
        self, httpx_mock: HTTPXMock, mode: AuthMode
    ) -> None:
        answer(
            httpx_mock,
            mode,
            "GET",
            PEERS,
            platform_error("unauthorized", INVALID_KEY),
            status=401,
            params=page_params(1),
        )

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert str(caught.value) == f"Failed to look up peer: {INVALID_KEY}"

    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize(("status", "body", "reported"), CHAT_REJECTIONS)
    async def test_room_rejection_reports_the_platform_message(
        self,
        httpx_mock: HTTPXMock,
        mode: AuthMode,
        status: int,
        body: dict[str, Any],
        reported: str,
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[TARGET]])
        answer(httpx_mock, mode, "POST", CHATS, body, status=status)

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert str(caught.value) == f"Failed to create chatroom: {reported}"

    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize(
        ("status", "body", "platform_message"), PARTICIPANT_REJECTIONS
    )
    async def test_participant_rejection_reports_the_message_and_orphan_room(
        self,
        httpx_mock: HTTPXMock,
        caplog: pytest.LogCaptureFixture,
        mode: AuthMode,
        status: int,
        body: dict[str, Any],
        platform_message: str,
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[TARGET]])
        answer(httpx_mock, mode, "POST", CHATS, created_room())
        answer(
            httpx_mock,
            mode,
            "POST",
            PARTICIPANTS,
            body,
            status=status,
        )

        caplog.set_level(logging.WARNING, logger="band.cli.trigger")

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert ORPHAN_WARNING in caplog.messages
        assert str(caught.value) == (
            f"Failed to complete trigger: {platform_message} (orphan room: {ROOM_ID})"
        )

    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_message_rejection_reports_the_message_and_orphan_room(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture, mode: AuthMode
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[TARGET]])
        answer(httpx_mock, mode, "POST", CHATS, created_room())
        answer(httpx_mock, mode, "POST", PARTICIPANTS, added_participant(TARGET))
        answer(
            httpx_mock,
            mode,
            "POST",
            MESSAGES,
            platform_error("forbidden", MESSAGE_FORBIDDEN),
            status=403,
        )

        caplog.set_level(logging.WARNING, logger="band.cli.trigger")

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert ORPHAN_WARNING in caplog.messages
        assert str(caught.value) == (
            f"Failed to complete trigger: {MESSAGE_FORBIDDEN} (orphan room: {ROOM_ID})"
        )
