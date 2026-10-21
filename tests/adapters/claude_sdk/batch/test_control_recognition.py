"""Recognizing a room command is read-only and agrees with its handler."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from tests.adapters.claude_sdk.batch.kit import ADMIN, LIST_FILES, OpenRoom
from tests.adapters.claude_sdk.helpers import room_message, with_approvals

COMMANDS = ["/approve a-1", "/decline", "/approvals", "/status", "@bob /approve a-1"]
ORDINARY_TEXT = ["list the files", "please /approve it", "/model sonnet", "/approving"]


def message(content: str) -> PlatformMessage:
    return room_message(content, message_id="m1")


class TestControlRecognition:
    @pytest.mark.parametrize("content", COMMANDS)
    def test_a_room_command_is_a_control(self, content: str) -> None:
        adapter = ClaudeSDKAdapter(with_approvals())

        assert adapter.is_control_message(message(content))

    @pytest.mark.parametrize("content", ORDINARY_TEXT)
    def test_other_text_is_not_a_control(self, content: str) -> None:
        adapter = ClaudeSDKAdapter(with_approvals())

        assert not adapter.is_control_message(message(content))

    @pytest.mark.parametrize("content", COMMANDS)
    def test_room_commands_are_ordinary_text_when_approvals_are_off(
        self, content: str
    ) -> None:
        assert not ClaudeSDKAdapter().is_control_message(message(content))

    @pytest.mark.parametrize("content", [*COMMANDS, *ORDINARY_TEXT])
    async def test_recognition_agrees_with_what_the_handler_consumes(
        self, content: str
    ) -> None:
        adapter = ClaudeSDKAdapter(with_approvals())
        msg = message(content)

        recognized = adapter.is_control_message(msg)

        assert recognized == await adapter.try_handle_control(msg, FakeAgentTools())

    @pytest.mark.parametrize("content", ["/approve a-1", "/decline a-9", "/approve"])
    async def test_an_unauthorized_sender_still_sends_a_control(
        self, content: str
    ) -> None:
        adapter = ClaudeSDKAdapter(with_approvals(authorized_senders={ADMIN["id"]}))

        assert adapter.is_control_message(message(content))

    async def test_recognizing_a_reply_leaves_the_pending_approval_unanswered(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, room.model_reply("Listed.")])
        await room.send("List the files")
        posted = list(room.tools.messages_sent)

        recognized = [
            room.adapter.is_control_message(room.message(command))
            for command in ("/approve a-1", "/decline a-1", "/approvals", "/status")
        ]

        assert recognized == [True] * 4
        assert room.tools.messages_sent == posted
        await room.send("/approvals")
        assert "- `a-1`:" in room.chat[-1]
        await room.send("/approve a-1")
        await room.settled()
        assert room.chat[-1] == "Listed."
