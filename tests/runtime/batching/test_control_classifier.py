"""The runtime classifies a delivery with its adapter's recognizer, consuming nothing."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import CodexAdapter
from band.adapters.cursor_acp import CursorACPAdapter
from band.core.protocols import FrameworkAdapter
from band.preprocessing.default import DefaultPreprocessor
from tests.adapters.claude_sdk.helpers import with_approvals
from tests.conftest import make_message_event
from tests.runtime.batching.kit import CONTACTS_NOTICE, offered_context, seeded_room
from tests.runtime.helpers import AGENT_ID, ROOM_ID

ADAPTERS: dict[str, Callable[[], FrameworkAdapter]] = {
    "claude": lambda: ClaudeSDKAdapter(with_approvals()),
    "codex": CodexAdapter,
    "cursor": CursorACPAdapter,
}
# One command each adapter recognizes, as a room delivers it.
COMMANDS = {
    "claude": "/approve a-1",
    "codex": "/approve req-1",
    "cursor": "@[[alice]] /cursor select abc allow-1",
}


class TestControlClassifier:
    @pytest.mark.parametrize(
        ("adapter", "content", "expected"),
        [
            ("claude", COMMANDS["claude"], True),
            ("claude", "@bob /decline a-1", True),
            ("claude", "/status", True),
            ("claude", "list the files", False),
            ("claude", "please /approve it", False),
            ("codex", COMMANDS["codex"], True),
            ("codex", "@bob /decline req-1", True),
            ("codex", "/model gpt-5", True),
            ("codex", "run the tests", False),
            ("codex", "please /approve it", False),
            ("cursor", COMMANDS["cursor"], True),
            ("cursor", "hello", False),
        ],
    )
    def test_a_delivery_is_a_control_exactly_when_its_adapter_says_so(
        self, adapter: str, content: str, expected: bool
    ) -> None:
        room = seeded_room(ADAPTERS[adapter]())
        event = make_message_event(room_id=ROOM_ID, content=content)

        assert room.recognizes_delivery_control(event) is expected

    @pytest.mark.parametrize("adapter", list(ADAPTERS))
    async def test_classifying_leaves_the_room_context_for_the_next_input(
        self, adapter: str
    ) -> None:
        room = seeded_room(ADAPTERS[adapter]())
        event = make_message_event(room_id=ROOM_ID, content=COMMANDS[adapter])

        assert room.recognizes_delivery_control(event)
        inp = await DefaultPreprocessor().process(room, event, AGENT_ID)

        assert inp is not None
        assert offered_context(inp) == (True, True, CONTACTS_NOTICE)
