"""What the agent sees when the room view reads a room."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band.integrations.desktop_app.service import AgentTranscriptTools
from band.integrations.desktop_app.settings import RoomViewTuning
from tests.integrations.desktop_app.conftest import (
    ROOM_ID,
    TOM,
    ids,
    mentioned_message,
    message,
)


def busy_room(count: int) -> list[dict[str, Any]]:
    """A room with more agent-visible history than one page holds."""
    return [
        message(f"m-{index:02d}", f"2026-01-01T00:{index:02d}:00Z")
        for index in range(count)
    ]


class TestReading:
    async def test_a_read_pages_back_until_it_reaches_the_cursor(
        self, room: Any
    ) -> None:
        """Whatever a long absence buried behind the newest page is still owed:
        dropping it while the cursor advances past it loses it for good."""
        live = room(
            busy_room(6),
            tuning=RoomViewTuning(band_transcript_page_size=2),
        )

        result = await live.read(since="2026-01-01T00:01:00Z")

        assert ids(result.messages) == ["m-01", "m-02", "m-03", "m-04", "m-05"]

    async def test_a_read_gives_up_paging_back_rather_than_read_a_history(
        self, room: Any
    ) -> None:
        """A cursor buried deeper than the budget is an absence, not a tick."""
        live = room(
            busy_room(60),
            tuning=RoomViewTuning(band_transcript_page_size=1),
        )

        result = await live.read(since="2026-01-01T00:00:00Z")

        assert len(result.messages) == 20, "the page budget bounds one read"
        assert result.messages[-1].id == "m-59", "it is the newest end that is kept"

    async def test_opening_a_room_reads_only_its_tail(self, room: Any) -> None:
        """A long room must not cost its whole history on join."""
        live = room(
            busy_room(60),
            tuning=RoomViewTuning(band_transcript_page_size=10),
        )

        result = await live.read()

        assert len(result.messages) == live.tuning.band_initial_transcript_messages
        assert result.messages[-1].id == "m-59"
        assert live.rest_calls == 4, (
            "three pages cover the opening 25; the first sizes the room"
        )

    async def test_a_cursor_hides_everything_already_seen(self, room: Any) -> None:
        live = room(
            [
                message("old", "2026-01-01T00:00:01Z"),
                message("new", "2026-01-01T00:00:03Z"),
            ]
        )

        result = await live.read(since="2026-01-01T00:00:02Z")

        assert ids(result.messages) == ["new"]

    async def test_an_oversized_message_is_truncated(self, room: Any) -> None:
        live = room([])
        oversized = message("m-1", "2026-01-01T00:00:01Z")
        oversized["content"] = "x" * (live.tuning.band_max_message_chars + 1)

        result = await room([oversized]).read()

        assert result.messages[0].content.endswith("… [truncated]")

    async def test_a_mention_past_the_size_limit_still_addresses_the_agent(
        self, room: Any
    ) -> None:
        live = room([])
        addressed = mentioned_message("m-1", "2026-01-01T00:00:01Z")
        addressed["metadata"] = {}
        addressed["content"] = (
            "x" * (live.tuning.band_max_message_chars + 1) + f" @[[{TOM['id']}]]"
        )

        result = await room([addressed]).read()

        assert result.messages[0].addressed_to_viewer
        assert ids(result.pending_requests) == ["m-1"]

    async def test_the_roster_is_cached_until_an_event_refreshes_it(
        self, room: Any
    ) -> None:
        """The roster changes only on a participant event; re-reading is waste."""
        live = room([], [], [])

        await live.read()
        await live.read()
        await live.read(refresh_participants=True)

        assert live.roster_reads == 2


class TestPendingRequests:
    async def test_only_unanswered_mentions_of_the_agent_are_pending(
        self, room: Any
    ) -> None:
        """The agent's own last message is the watermark: older asks are done."""
        live = room(
            [
                mentioned_message(
                    "old-request",
                    "2026-01-01T00:00:01Z",
                    sender_id="human-1",
                    sender_name="Alexander",
                ),
                {
                    **message("agent-reply", "2026-01-01T00:00:02Z"),
                    "sender_id": "agent-1",
                },
                mentioned_message("pending-request", "2026-01-01T00:00:03Z"),
            ]
        )

        result = await live.read()

        assert ids(result.pending_requests) == ["pending-request"]
        assert result.messages[0].addressed_to_viewer is True
        assert result.messages[1].addressed_to_viewer is False

    async def test_a_reply_to_one_peer_leaves_another_peer_still_waiting(
        self, room: Any
    ) -> None:
        """A resumed read holds only messages the agent has never been shown,
        so a reply inside it cannot have answered an ask it had not yet seen."""
        live = room(
            [
                mentioned_message(
                    "human-ask",
                    "2026-01-01T00:00:03Z",
                    sender_id="human-1",
                    sender_name="Alexander",
                ),
                {
                    **message("reply-to-jerry", "2026-01-01T00:00:04Z"),
                    "sender_id": "agent-1",
                },
            ]
        )

        result = await live.read(since="2026-01-01T00:00:02Z")

        assert ids(result.pending_requests) == ["human-ask"]


class TestRestBoundary:
    async def test_room_creation_omits_an_absent_task_id(self) -> None:
        rest: Any = MagicMock()
        rest.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=[
                SimpleNamespace(data=SimpleNamespace(id="room-without-task")),
                SimpleNamespace(data=SimpleNamespace(id="room-with-task")),
            ]
        )
        tools = AgentTranscriptTools(rest)

        assert await tools.create_room() == "room-without-task"
        assert await tools.create_room("task-1") == "room-with-task"

        calls = rest.agent_api_chats.create_agent_chat.await_args_list
        assert calls[0].kwargs["chat"].model_dump(exclude_unset=True) == {}
        assert calls[1].kwargs["chat"].model_dump(exclude_unset=True) == {
            "task_id": "task-1"
        }

    async def test_reads_go_through_the_sdk_room_tools(self) -> None:
        """The view must not grow a second copy of the agent REST reads."""
        rest: Any = MagicMock()
        pagination = MagicMock()
        pagination.model_dump.return_value = {"total_pages": 3}
        rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=SimpleNamespace(data=[], metadata=pagination)
        )
        participant = MagicMock()
        participant.model_dump.return_value = {"id": "human-1", "type": "User"}
        rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=SimpleNamespace(data=[participant])
        )
        profile = MagicMock()
        profile.model_dump.return_value = {"data": {"id": "agent-1", "name": "tom"}}
        rest.agent_api_identity.get_agent_me = AsyncMock(return_value=profile)
        reads = AgentTranscriptTools(rest)

        messages, metadata = await reads.list_agent_context(
            ROOM_ID, page=1, page_size=25
        )

        assert await reads.get_agent_profile() == {"id": "agent-1", "name": "tom"}
        assert (messages, metadata["total_pages"]) == ([], 3)
        assert await reads.list_participants(ROOM_ID) == [
            {"id": "human-1", "type": "User"}
        ]
