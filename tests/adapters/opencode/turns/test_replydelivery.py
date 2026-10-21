from __future__ import annotations

import asyncio

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools, events_of_type, failure_reports
from tests.adapters.dispatch import deliver_event, deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    agent_input,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)


class TestReplyDelivery:
    async def test_a_reply_that_cannot_be_posted_is_reported_once(
        self, make_adapter, tools
    ) -> None:
        """The delivery failure is the turn's report; the verdict adds no second."""
        tools.send_message_error = RuntimeError("no one to mention")
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-assistant"),
                    event_text_part("sess-1", "msg-assistant", "pong"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_event(adapter, agent_input("ping", tools))

        assert [provider for provider, _ in failure_reports(tools)] == ["opencode"]

    async def test_manual_relay_releases_turn_when_mentionless_send_rejected(
        self, make_adapter, tools
    ) -> None:
        """A sender-less turn yields no mentions, which the platform rejects.
        The manual approval relay must still release the turn (best-effort
        post) rather than stranding on_message until the turn timeout."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-loop", permission="doom_loop")]
            ],
            reply_permission_events={"perm-loop": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        # No hang: on_message returns once the relay releases the turn wait,
        # even though the mention-less room post was rejected.
        await asyncio.wait_for(
            deliver_message(
                adapter,
                make_platform_message(sender_id="", sender_name=""),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            ),
            timeout=5,
        )

        # Relay was attempted and dropped (nothing recorded), and manual mode
        # did not auto-reply the pending permission.
        assert tools.messages_sent == []
        assert fake_client.permission_replies == []
        await adapter.on_cleanup("room-1")

    async def test_turn_completes_when_fallback_reply_send_rejected(
        self, make_adapter, tools
    ) -> None:
        """A sender-less turn's reply has no one to @mention, so the platform
        rejects it. The watch task must still release on_message (release is
        in finally) instead of stranding it on the captured release_future."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "here is the reply"),
                    event_session_idle("sess-1"),
                ]
            ],
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await asyncio.wait_for(
            deliver_message(
                adapter,
                make_platform_message(sender_id="", sender_name=""),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            ),
            timeout=5,
        )

        # The reply could not be delivered (no mention), so it surfaced as an
        # error event rather than hanging or vanishing silently.
        assert tools.messages_sent == []
        assert events_of_type(tools, "error")
        await adapter.on_cleanup("room-1")
