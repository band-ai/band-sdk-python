"""A room's recorded OpenCode session is resumed, whatever the emit setting."""

from __future__ import annotations

from collections.abc import Callable

from band.adapters.opencode import OpencodeAdapter
from tests.adapters.opencode.helpers import FakeOpencodeClient, event_session_error
from tests.adapters.opencode.resume.kit import (
    ROOM_ID,
    TRANSCRIPT,
    answer,
    prompts,
    recorded,
    room_input,
    room_tools,
    started,
)


class TestRestore:
    async def test_a_restart_resumes_the_session_seeded_with_task_events_off(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        history = await recorded(make_adapter)

        restarted_client = FakeOpencodeClient(
            prompt_event_sequences=[answer("sess-1", "msg-2")]
        )
        restarted = await started(make_adapter(restarted_client, emit=()))
        restarted_tools = room_tools()
        await restarted.on_event(
            room_input(restarted_tools, history=history, bootstrap=True)
        )

        assert restarted_client.created_sessions == []
        assert prompts(restarted_client) == [("sess-1", False)]
        assert restarted_tools.events_sent == []

    async def test_a_failed_prompt_keeps_the_restored_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-1", "msg-2"),
            ]
        )
        history = await recorded(make_adapter)
        adapter = await started(make_adapter(client))

        await adapter.on_event(
            room_input(room_tools(), history=history, bootstrap=True)
        )
        await adapter.on_event(
            room_input(room_tools(), history=history, bootstrap=False)
        )

        assert client.created_sessions == []
        assert prompts(client) == [("sess-1", False), ("sess-1", False)]

    async def test_a_record_from_an_earlier_release_starts_one_replayed_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        """Earlier releases recorded a session when it was created, before its
        seeding prompt ran, so such a record may name an unseeded session."""
        legacy_record = {
            "id": "evt-legacy",
            "message_type": "task",
            "content": "OpenCode session created: `sess-old`",
            "sender_id": "agent-1",
            "sender_type": "Agent",
            "metadata": {
                "opencode_session_id": "sess-old",
                "opencode_mcp_server_name": None,
                "opencode_room_id": ROOM_ID,
                "opencode_created_at": "2026-10-01T00:00:00+00:00",
            },
        }
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                answer("sess-1", "msg-1"),
                answer("sess-1", "msg-2"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))

        await adapter.on_event(
            room_input(
                room_tools(), history=[*TRANSCRIPT, legacy_record], bootstrap=True
            )
        )
        await adapter.on_event(room_input(room_tools(), history=[], bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-1", False)]
