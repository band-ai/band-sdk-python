"""A non-platform failure after the room exists still names the room for cleanup."""

from __future__ import annotations

import logging

import httpx
import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import AuthMode, run
from tests.cli.triggerwire.kit import (
    CHATS,
    PARTICIPANTS,
    ROOM_ID,
    TARGET,
    answer,
    answer_peer_pages,
    api_url,
    created_room,
    trigger_args,
)

ORPHAN_WARNING = f"Failed after creating room {ROOM_ID} — room may need manual cleanup"


class TestOrphanRoom:
    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_transport_failure_after_room_creation_warns_of_the_orphan(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture, mode: AuthMode
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[TARGET]])
        answer(httpx_mock, mode, "POST", CHATS, created_room())
        httpx_mock.add_exception(
            httpx.ConnectError("connection reset"),
            method="POST",
            url=api_url(mode, PARTICIPANTS),
        )
        caplog.set_level(logging.WARNING, logger="band.cli.trigger")

        with pytest.raises(httpx.ConnectError):
            await run(trigger_args(mode, target_handle=f"@{TARGET['handle']}"))

        assert ORPHAN_WARNING in caplog.messages
