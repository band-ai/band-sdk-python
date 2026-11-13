"""band-trigger reports the platform's own error message, whether or not Fern types the status."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import run
from tests.cli.triggerwire.kit import (
    ROOM_ID,
    TARGET,
    AuthMode,
    answer,
    created_room,
    page_params,
    peers_page,
    platform_error,
    trigger_args,
    validation_error,
)

ROOM_LIMIT = "Chat room limit reached."
INVALID_CHAT = "Validation failed"
UNKNOWN_PARTICIPANT = "Participant not found"
INVALID_PARTICIPANT = "Participant cannot join this room"

# 403/404 parse into Fern's ``Error`` model; an undeclared or ``Any``-typed
# status such as 422 leaves the body a plain dict.
CHAT_REJECTIONS = [
    pytest.param(403, platform_error("forbidden", ROOM_LIMIT), ROOM_LIMIT, id="403"),
    pytest.param(
        422,
        validation_error(INVALID_CHAT, {"/chat/title": ["is invalid"]}),
        INVALID_CHAT,
        id="422",
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


def find_target(httpx_mock: HTTPXMock, mode: AuthMode) -> None:
    answer(
        httpx_mock,
        mode,
        "GET",
        "/peers",
        peers_page([TARGET], page=1, total_pages=1),
        params=page_params(1),
    )


class TestTriggerPlatformErrors:
    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize(("status", "body", "platform_message"), CHAT_REJECTIONS)
    async def test_room_rejection_reports_the_platform_message(
        self,
        httpx_mock: HTTPXMock,
        mode: AuthMode,
        status: int,
        body: dict[str, Any],
        platform_message: str,
    ) -> None:
        find_target(httpx_mock, mode)
        answer(httpx_mock, mode, "POST", "/chats", body, status=status)

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert str(caught.value) == f"Failed to create chatroom: {platform_message}"

    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize(
        ("status", "body", "platform_message"), PARTICIPANT_REJECTIONS
    )
    async def test_participant_rejection_reports_the_message_and_orphan_room(
        self,
        httpx_mock: HTTPXMock,
        mode: AuthMode,
        status: int,
        body: dict[str, Any],
        platform_message: str,
    ) -> None:
        find_target(httpx_mock, mode)
        answer(httpx_mock, mode, "POST", "/chats", created_room())
        answer(
            httpx_mock,
            mode,
            "POST",
            f"/chats/{ROOM_ID}/participants",
            body,
            status=status,
        )

        with pytest.raises(RuntimeError) as caught:
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert str(caught.value) == (
            f"Failed to complete trigger: {platform_message} (orphan room: {ROOM_ID})"
        )
