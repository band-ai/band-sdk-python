"""Recognizing a local command is read-only and never lets one ride in a batch."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter, CodexCommand
from band.testing import FakeAgentTools
from tests.adapters.codexbatch.kit import (
    ROOM_ID,
    OpenRoom,
    approval_request,
    room_message,
)
from tests.adapters.codexturns import turn_completed

COMMANDS = [f"/{command}" for command in CodexCommand]
ORDINARY_TEXT = ["list the files", "please /approve it", "/unknown", "/approving"]
# What try_handle_control consumes; every other command is answered by its own turn.
CONSUMED_AS_CONTROL = {
    CodexCommand.APPROVE,
    CodexCommand.APPROVE_SESSION,
    CodexCommand.DECLINE,
    CodexCommand.APPROVALS,
    CodexCommand.STATUS,
}


def tables_of(adapter: CodexAdapter) -> dict[str, Any]:
    """A snapshot of every table the adapter keeps."""
    return {
        name: copy.copy(value)
        for name, value in vars(adapter).items()
        if isinstance(value, dict | set | list)
    }


class TestControlRecognition:
    @pytest.mark.parametrize("content", [*COMMANDS, "@bob /approve req-1"])
    def test_every_local_command_is_recognized(self, content: str) -> None:
        msg = room_message(content, message_id="m1")

        assert CodexAdapter().is_control_message(msg)

    @pytest.mark.parametrize(
        "content", ["/approve no-such-id", "/approve", "/sandbox nonsense"]
    )
    def test_an_invalid_command_is_still_recognized(self, content: str) -> None:
        msg = room_message(content, message_id="m1")

        assert CodexAdapter().is_control_message(msg)

    @pytest.mark.parametrize("content", ORDINARY_TEXT)
    def test_other_text_is_not_a_control(self, content: str) -> None:
        msg = room_message(content, message_id="m1")

        assert not CodexAdapter().is_control_message(msg)

    @pytest.mark.parametrize("command", CodexCommand)
    async def test_the_handler_consumes_only_approvals_and_status(
        self, command: CodexCommand
    ) -> None:
        adapter = CodexAdapter()
        msg = room_message(f"/{command}", message_id="m1")

        consumed = await adapter.try_handle_control(msg, FakeAgentTools())

        assert consumed is (command in CONSUMED_AS_CONTROL)

    async def test_recognizing_creates_no_room_state(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room()
        before = tables_of(room.adapter)

        recognized = [
            room.adapter.is_control_message(room.message(content))
            for content in COMMANDS
        ]

        assert recognized == [True] * len(COMMANDS)
        assert tables_of(room.adapter) == before
        assert room.adapter._room_clients == {}

    async def test_recognizing_a_reply_leaves_the_pending_approval_unanswered(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(approval_request(10), turn_completed())
        await room.send("run tests")
        before = tables_of(room.adapter)
        posted = list(room.chat)

        recognized = [
            room.adapter.is_control_message(room.message(content))
            for content in ("/approve req-10", "/decline req-10", "/approvals")
        ]

        pending = room.adapter._pending_approvals[ROOM_ID]
        assert recognized == [True] * 3
        assert [entry.token for entry in pending.unclaimed()] == ["req-10"]
        assert not pending.get("req-10").future.done()
        assert room.decisions == []
        assert room.chat == posted
        assert tables_of(room.adapter) == before
        await room.control("/approve req-10")
        await room.settled()
        assert room.decisions == [(10, "accept")]
