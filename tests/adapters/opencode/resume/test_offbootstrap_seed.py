"""A session created after bootstrap is seeded with the re-fetched room transcript."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.converters.opencode import OPENCODE_RESUME_RECORD
from band.core.types import MessageType
from band.testing import FakeAgentTools
from tests.adapters.audiences import BOB, ROOM_MEMBERS
from tests.adapters.opencode.helpers import FakeOpencodeClient, event_session_error
from tests.adapters.opencode.resume.kit import (
    LATER,
    QUESTION,
    ROOM_ID,
    TRANSCRIPT,
    answer,
    prompt_text,
    prompts,
    room_input,
    room_tools,
    started,
)
from tests.roomhistory import DEFAULT_HISTORY_INSERTED_AT, stored


class TestOffBootstrapSeed:
    async def test_a_vanished_live_session_is_replaced_and_seeded_with_earlier_history(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                answer("sess-1", "msg-1"),
                answer("sess-2", "msg-2"),
            ],
            get_session_missing={"sess-1"},
        )
        adapter = await started(make_adapter(client, emit=()))
        reseeded = room_tools()

        await adapter.on_event(
            room_input(room_tools(), history=TRANSCRIPT, bootstrap=True)
        )
        await adapter.on_event(room_input(reseeded, history=[], bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-2", True)]
        reseed = prompt_text(client.prompt_calls[1])
        assert LATER["content"] not in reseed
        assert reseed.count(QUESTION.content) == 1
        assert OPENCODE_RESUME_RECORD.candidates(reseeded.events_sent) == {
            ROOM_ID: "sess-2"
        }

    @pytest.mark.parametrize(
        ("fetch_error", "context"),
        [
            (RuntimeError("context unavailable"), []),
            (None, [stored("unknown ordering", inserted_at=None)]),
        ],
        ids=["fetch-failed", "cutoff-unavailable"],
    )
    async def test_a_prompt_sent_without_the_transcript_records_nothing(
        self,
        make_adapter: Callable[..., OpencodeAdapter],
        fetch_error: Exception | None,
        context: list[dict[str, Any]],
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-2", "msg-2"),
                answer("sess-3", "msg-3"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))
        unreachable, retried = room_tools(), room_tools()
        unreachable.fetch_room_context_error = fetch_error
        unreachable.set_room_context(context)

        await adapter.on_event(
            room_input(room_tools(), history=TRANSCRIPT, bootstrap=True)
        )
        await adapter.on_event(room_input(unreachable, history=[], bootstrap=False))
        await adapter.on_event(room_input(retried, history=[], bootstrap=False))

        assert prompts(client) == [
            ("sess-1", True),
            ("sess-2", False),
            ("sess-3", True),
        ]
        assert OPENCODE_RESUME_RECORD.candidates(unreachable.events_sent) == {}
        assert OPENCODE_RESUME_RECORD.candidates(retried.events_sent) == {
            ROOM_ID: "sess-3"
        }

    async def test_the_replay_names_mentioned_participants_by_handle(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        mention = {
            "id": "msg-mention",
            "message_type": MessageType.TEXT,
            "content": f"@[[{BOB['id']}]] the code word is heron.",
            "inserted_at": DEFAULT_HISTORY_INSERTED_AT,
            "sender_id": "user-1",
            "sender_name": "Alice",
            "sender_type": "User",
        }
        client = FakeOpencodeClient(prompt_event_sequences=[answer("sess-1", "msg-1")])
        adapter = await started(make_adapter(client, emit=()))
        tools = FakeAgentTools(participants=ROOM_MEMBERS, room_context=[mention])

        await adapter.on_event(room_input(tools, history=[], bootstrap=False))

        seed = prompt_text(client.prompt_calls[0])
        assert f"@{BOB['handle']} the code word is heron." in seed
        assert f"@[[{BOB['id']}]]" not in seed
