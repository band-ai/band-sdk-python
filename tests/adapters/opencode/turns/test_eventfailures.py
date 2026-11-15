from __future__ import annotations

from band.integrations.opencode.types import OpencodeSessionState
from band.testing import reported_failures
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    TaskEventFailingTools,
    event_message_updated,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)


class TestEventFailures:
    async def test_malformed_events_do_not_kill_event_loop(
        self, make_adapter, tools
    ) -> None:
        """Junk SSE payloads degrade to ignored events; the turn that follows
        them completes normally instead of the event loop dying mid-stream."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    {"type": "bizarre.event", "properties": {"sessionID": "sess-1"}},
                    {"type": "permission.asked", "properties": "garbage"},
                    {},
                    {"type": "message.updated", "properties": {"info": "not-a-dict"}},
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "survived the junk"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert any(msg["content"] == "survived the junk" for msg in tools.messages_sent)

        await adapter.on_cleanup("room-1")

    async def test_task_event_post_failure_does_not_drop_the_turn(
        self, make_adapter
    ) -> None:
        """A transient failure posting the session task event must not abort the
        turn before the model runs -- otherwise the user's message is silently
        dropped. The event is best-effort; the prompt still goes out and the reply
        lands."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part(
                        "sess-1", "msg-1", "Handled despite the event failure."
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)
        tools = TaskEventFailingTools(participants=ROOM_MEMBERS)

        await run_single_turn(adapter, tools)

        assert len(fake_client.prompt_calls) == 1
        assert tools.chat == ["Handled despite the event failure."]
        assert not any(
            "failed while processing" in f["message"].lower()
            for f in reported_failures(tools)
        )
