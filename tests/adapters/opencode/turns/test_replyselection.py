from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.wording import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_REQUESTED_TEMPLATE,
)
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.dispatch import deliver_event, deliver_message
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    agent_input,
    event_message_updated,
    event_part_delta,
    event_permission,
    event_reasoning_part,
    event_session_idle,
    event_text_part,
    event_tool_part,
    event_user_message_updated,
    make_platform_message,
    run_single_turn,
    tools_protocol,
    wait_for,
)


class TestReplySelection:
    async def test_does_not_echo_user_text_parts_as_assistant_output(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_user_message_updated("sess-1", "msg-user"),
                    event_text_part("sess-1", "msg-user", "user prompt text"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_event(adapter, agent_input("user prompt text", tools))

        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_ignores_reasoning_deltas_and_relays_final_text_only(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-assistant"),
                    event_reasoning_part(
                        "sess-1",
                        "msg-assistant",
                        part_id="part-reasoning",
                    ),
                    event_part_delta(
                        "sess-1",
                        "msg-assistant",
                        "part-reasoning",
                        'The user wants "pong".',
                    ),
                    event_text_part("sess-1", "msg-assistant", ""),
                    event_part_delta(
                        "sess-1",
                        "msg-assistant",
                        "part-msg-assistant",
                        "pong",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="Reply with exactly: pong"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent[0]["content"] == "pong"

    async def test_room_posting_tool_reply_suppresses_text_fallback(
        self, make_adapter, tools, mcp_backend: BandMCPCalls
    ) -> None:
        """When the model replies via band_send_message, the adapter must not also
        post the assistant's plain text (double-post)."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "I sent it via the tool."),
                    mcp_backend.band_tool_call(
                        BandTool.SEND_MESSAGE, {"content": "hi", "mentions": ["@alice"]}
                    ),
                    event_tool_part(
                        "sess-1",
                        "msg-1",
                        tool="band_send_message",
                        call_id="c1",
                        status="completed",
                        input_data={"content": "hi"},
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await run_single_turn(adapter, tools)

        assert tools.chat == ["hi"]

    async def test_approval_prompts_never_stand_in_for_the_models_reply(
        self, make_adapter, tools
    ) -> None:
        """The permission prompt and its confirmation post on the turn's own tools
        mid-turn. They are adapter notices, so the model's final text is still
        relayed: the turn ends with exactly one model reply."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_permission("sess-1", "req-1"),
                ]
            ],
            reply_permission_events={
                "req-1": [
                    event_text_part("sess-1", "msg-1", "Approved and done."),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = make_adapter(fake_client)

        # Returns once the permission ask releases the turn wait.
        await run_single_turn(adapter, tools, content="run it")
        await deliver_message(
            adapter,
            make_platform_message(content="approve req-1"),
            tools_protocol(FakeAgentTools()),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await wait_for(lambda: tools.turn.replied)

        assert tools.chat == [
            APPROVAL_REQUESTED_TEMPLATE.format(
                permission="bash", patterns="rm -rf tmp", request_id="req-1"
            ),
            APPROVAL_HANDLED_TEMPLATE.format(request_id="req-1", reply="once"),
            "Approved and done.",
        ]
        assert tools.turn.complete

    async def test_non_room_posting_tool_does_not_suppress_text(
        self, make_adapter, tools
    ) -> None:
        """A non-posting tool (bash) is not a reply, so the assistant text is still
        delivered -- suppression must not over-reach."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "Ran the command."),
                    event_tool_part(
                        "sess-1",
                        "msg-1",
                        tool="bash",
                        call_id="c1",
                        status="completed",
                        input_data={"command": "ls"},
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client)

        await run_single_turn(adapter, tools)

        assert tools.chat == ["Ran the command."]
