"""A room's recorded OpenCode session is resumed, whatever the emit setting."""

from __future__ import annotations

from collections.abc import Callable

from band.adapters.opencode import OpencodeAdapter
from tests.adapters.opencode.helpers import FakeOpencodeClient, event_session_error
from tests.adapters.opencode.resume.kit import (
    TRANSCRIPT,
    answer,
    prompts,
    recorded,
    room_input,
    room_tools,
)

AGENT_NAME = "OpenCode Agent"


class TestRestore:
    async def test_a_restart_resumes_the_session_seeded_with_task_events_off(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        seeding_client = FakeOpencodeClient(
            prompt_event_sequences=[answer("sess-1", "msg-1")]
        )
        seeding = make_adapter(seeding_client, emit=())
        await seeding.on_started(AGENT_NAME, "A coding agent")
        seeded = room_tools()
        await seeding.on_event(room_input(seeded, history=TRANSCRIPT, bootstrap=True))

        restarted_client = FakeOpencodeClient(
            prompt_event_sequences=[answer("sess-1", "msg-2")]
        )
        restarted = make_adapter(restarted_client, emit=())
        await restarted.on_started(AGENT_NAME, "A coding agent")
        await restarted.on_event(
            room_input(
                room_tools(), history=[*TRANSCRIPT, *seeded.events_sent], bootstrap=True
            )
        )

        assert restarted_client.created_sessions == []
        assert prompts(restarted_client) == [("sess-1", False)]

    async def test_a_failed_prompt_keeps_the_restored_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-1", "msg-2"),
            ]
        )
        adapter = make_adapter(client)
        await adapter.on_started(AGENT_NAME, "A coding agent")
        history = await recorded("sess-1")

        await adapter.on_event(
            room_input(room_tools(), history=history, bootstrap=True)
        )
        await adapter.on_event(
            room_input(room_tools(), history=history, bootstrap=False)
        )

        assert client.created_sessions == []
        assert prompts(client) == [("sess-1", False), ("sess-1", False)]
