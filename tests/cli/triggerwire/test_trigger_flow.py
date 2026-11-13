"""band-trigger finds its target across peer pages and posts one mentioning message into a new room."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import run
from tests.cli.triggerwire.kit import (
    API_PREFIX,
    BYSTANDER,
    MESSAGE,
    ROOM_ID,
    TARGET,
    AuthMode,
    WireCall,
    added_participant,
    answer,
    answer_peer_pages,
    created_room,
    message_sent,
    page_params,
    trigger_args,
    wire_calls,
)


class TestTriggerFlow:
    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_mentions_the_target_found_on_a_later_page_in_a_new_room(
        self, httpx_mock: HTTPXMock, mode: AuthMode
    ) -> None:
        prefix = API_PREFIX[mode]
        answer_peer_pages(httpx_mock, mode, [[BYSTANDER], [TARGET]])
        answer(httpx_mock, mode, "POST", "/chats", created_room())
        answer(
            httpx_mock,
            mode,
            "POST",
            f"/chats/{ROOM_ID}/participants",
            added_participant(TARGET),
        )
        answer(
            httpx_mock,
            mode,
            "POST",
            f"/chats/{ROOM_ID}/messages",
            message_sent(TARGET),
        )

        room_id = await run(trigger_args(mode, target_handle="@Owner/Reporter"))

        assert room_id == ROOM_ID
        assert wire_calls(httpx_mock.get_requests()) == [
            WireCall("GET", f"{prefix}/peers", page_params(1), None),
            WireCall("GET", f"{prefix}/peers", page_params(2), None),
            WireCall("POST", f"{prefix}/chats", {}, {"chat": {}}),
            WireCall(
                "POST",
                f"{prefix}/chats/{ROOM_ID}/participants",
                {},
                {"participant": {"participant_id": TARGET["id"]}},
            ),
            WireCall(
                "POST",
                f"{prefix}/chats/{ROOM_ID}/messages",
                {},
                {
                    "message": {
                        "content": MESSAGE,
                        "mentions": [{"id": TARGET["id"], "handle": TARGET["handle"]}],
                    }
                },
            ),
        ]

    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_unknown_handle_scans_every_page_and_creates_no_room(
        self, httpx_mock: HTTPXMock, mode: AuthMode
    ) -> None:
        prefix = API_PREFIX[mode]
        answer_peer_pages(httpx_mock, mode, [[BYSTANDER], [TARGET]])

        with pytest.raises(ValueError, match="'@owner/absent' not found"):
            await run(trigger_args(mode, target_handle="@owner/absent"))

        assert wire_calls(httpx_mock.get_requests()) == [
            WireCall("GET", f"{prefix}/peers", page_params(1), None),
            WireCall("GET", f"{prefix}/peers", page_params(2), None),
        ]
