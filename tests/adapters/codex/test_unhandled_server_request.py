"""Codex adapter: a server request Band does not handle is refused, never left waiting."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import CodexRoom, RefusalFailsClient
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    event_request,
    tool_call_request,
    turn_completed,
)
from tests.adapters.waiting import until

# JSON-RPC's standard code for a method the receiver does not implement.
METHOD_NOT_FOUND = -32601
UNKNOWN_METHOD = "item/unknown/request"


def unknown_request(request_id: int, **params: Any) -> RpcEvent:
    return event_request(request_id, UNKNOWN_METHOD, params)


def from_autonomous_turn(request_id: int) -> RpcEvent:
    """An unknown request from a native turn no Band turn started."""
    return unknown_request(request_id, threadId=FIRST_THREAD_ID, turnId="autonomous")


def failed_refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Warnings logged with the error that kept a refusal from the process."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and record.exc_info is not None
    ]


class TestUnhandledServerRequest:
    @pytest.mark.asyncio
    async def test_a_turn_refuses_an_unknown_request_and_still_finishes(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            unknown_request(5),
            tool_call_request(1, BandTool.NO_REPLY),
            turn_completed(),
        )

        await room.send("work")

        assert room.client.error_codes == {5: METHOD_NOT_FOUND}
        assert [call["tool_name"] for call in room.deliveries[0].tool_calls] == [
            BandTool.NO_REPLY
        ]

    @pytest.mark.asyncio
    async def test_an_unknown_request_outside_any_turn_is_refused(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY), turn_completed()
        )
        await room.send("work")

        room.client.emit(from_autonomous_turn(6))

        await until(
            lambda: room.client.error_codes == {6: METHOD_NOT_FOUND},
            failure="The unknown request was never refused",
        )

    @pytest.mark.asyncio
    async def test_a_refusal_the_process_cannot_take_is_logged_and_the_room_serves_on(
        self,
        codex_room: Callable[..., Awaitable[CodexRoom]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = RefusalFailsClient(
            events=[
                tool_call_request(1, BandTool.NO_REPLY),
                turn_completed(),
                tool_call_request(2, BandTool.NO_REPLY),
                turn_completed("turn-2"),
            ]
        )
        room = await codex_room(client=client)
        await room.send("work")

        client.emit(from_autonomous_turn(6))
        await until(
            lambda: any(UNKNOWN_METHOD in log for log in failed_refusals(caplog)),
            failure="The failed refusal was never logged",
        )
        await room.send("more work")

        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
