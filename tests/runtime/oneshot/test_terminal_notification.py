"""OneShotInvoker: a deadline notice sent in this agent's name still runs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from band.client.streaming import MessageCreatedPayload
from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
)
from tests.runtime.oneshot.kit import make_adapter_mock
from tests.runtime.resync.test_terminal_notification import (
    MESSAGE_ID,
    SESSION_ID,
    terminal_row,
)


def forwarded(row: dict[str, Any]) -> dict[str, Any]:
    """The bridge envelope for one row, serialized as the bridge does."""
    payload = MessageCreatedPayload.model_validate(row).model_dump(mode="json")
    return {
        "event_type": "message_created",
        "agent_id": AGENT_ID,
        "room_id": ROOM_ID,
        "payload": payload,
    }


def turns(adapter: MagicMock) -> list[str]:
    return [call.args[0].msg.id for call in adapter.on_event.await_args_list]


@asynccontextmanager
async def invoker_over(
    peer: LifecyclePlatform,
) -> AsyncIterator[tuple[OneShotInvoker, MagicMock]]:
    adapter = make_adapter_mock()
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
        await invoker.startup()
        try:
            yield invoker, adapter
        finally:
            await invoker.shutdown()


class TestTerminalNotification:
    async def test_forwarded_notice_runs_once_and_later_turns_follow(self) -> None:
        peer = LifecyclePlatform()
        notice = terminal_row(peer)
        peer.add_message("msg-later")
        later = peer.messages[-1]

        async with invoker_over(peer) as (invoker, adapter):
            outcomes = [
                (await invoker.handle_event(forwarded(row)))["status"]
                for row in (notice, notice, later)
            ]

        assert outcomes == ["done", "already_processed", "done"]
        assert turns(adapter) == [MESSAGE_ID, "msg-later"]
        assert peer.marked("processed") == [MESSAGE_ID, "msg-later"]

    async def test_drain_stops_at_a_notice_queued_after_its_snapshot(self) -> None:
        peer = LifecyclePlatform()
        peer.add_message("msg-1")
        first = peer.messages[-1]
        notice = terminal_row(peer)

        async with invoker_over(peer) as (invoker, adapter):
            turn = await invoker.handle_event(forwarded(first))
            deadline = await invoker.handle_event(forwarded(notice))

        assert turn == {"status": "done", "room_id": ROOM_ID, "message_id": "msg-1"}
        assert deadline["status"] == "done"
        assert turns(adapter) == ["msg-1", MESSAGE_ID]
        assert peer.marked("processed") == ["msg-1", MESSAGE_ID]

    @pytest.mark.parametrize("tamper", ["text", "no_recipient", "wrong_message"])
    async def test_ordinary_self_echo_and_forged_metadata_stay_skipped(
        self, tamper: str
    ) -> None:
        peer = LifecyclePlatform()
        row = terminal_row(peer)
        match tamper:
            case "text":
                row["message_type"] = "text"
            case "no_recipient":
                row["metadata"]["mentions"] = []
            case "wrong_message":
                row["metadata"]["contract_terminal"]["message"] = SESSION_ID

        async with invoker_over(peer) as (invoker, adapter):
            result = await invoker.handle_event(forwarded(row))

        assert result["status"] == "skipped_self"
        assert turns(adapter) == []
        assert peer.accepted_marks == []
