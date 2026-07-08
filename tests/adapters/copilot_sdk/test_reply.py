"""Final-reply delivery: mentions, prompt shape, and failure surfacing."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation
    from copilot.generated.session_events import SessionErrorData


class TestReply:
    @pytest.mark.asyncio
    async def test_reply_sent_with_sender_mention(self):
        client = FakeCopilotClient(reply_content="Hi Alice!")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        assert len(tools.messages_sent) == 1
        sent = tools.messages_sent[0]
        assert sent["content"] == "Hi Alice!"
        assert sent["mentions"] == [{"id": "user-1", "name": "Alice"}]

    @pytest.mark.asyncio
    async def test_prompt_contains_room_context_and_message(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools, content="What's up?")

        prompt = client.sessions[0].prompts[0]
        assert "[room_id: room-1]" in prompt
        assert "[Alice]: What's up?" in prompt

    @pytest.mark.asyncio
    async def test_no_reply_raises_and_reports_error(self):
        client = FakeCopilotClient(reply_content=None)
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="no reply"):
            await run_message(adapter, tools)

        assert not tools.messages_sent
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert error_events

    @pytest.mark.asyncio
    async def test_session_error_raises_reports_and_evicts(self):
        """A session error makes send_and_wait raise (the real SDK has no
        non-fatal error path): the adapter must report it, evict the
        session, and re-raise — not fall through to the no-reply branch."""
        client = FakeCopilotClient(
            turn_events=[SessionErrorData(error_type="model_error", message="boom")],
        )
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(Exception, match="boom"):
            await run_message(adapter, tools)

        session = client.sessions[0]
        assert session.aborted and session.disconnected
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert error_events and "boom" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_fallback_send_suppressed_when_band_send_message_fired(self):
        async def model_sends_message(session: FakeCopilotSession) -> None:
            handler = session.find_tool("band_send_message").handler
            await handler(
                ToolInvocation(
                    tool_call_id="call-1",
                    tool_name="band_send_message",
                    arguments={"content": "sent via tool", "mentions": ["user-1"]},
                )
            )

        client = FakeCopilotClient(
            reply_content="Duplicate reply", turn_events=[model_sends_message]
        )
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        # Tool call executed, but the adapter must not also send its final text.
        assert tools.tool_calls == [
            {
                "tool_name": "band_send_message",
                "arguments": {"content": "sent via tool", "mentions": ["user-1"]},
            }
        ]
        assert not tools.messages_sent

    @pytest.mark.asyncio
    async def test_fallback_fires_when_band_send_message_fails(self):
        """A failed band_send_message (ok=False, no exception) must NOT mark the turn
        replied — the final-text fallback must still fire, else the user gets a silent
        turn."""
        from band.runtime.tools import ToolCallOutcome

        class SendFailsTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(self, tool_name, arguments):
                if tool_name == "band_send_message":
                    return ToolCallOutcome(
                        value="Error executing band_send_message: upstream 500",
                        ok=False,
                        error_message="upstream 500",
                    )
                return await super().execute_tool_call_structured(tool_name, arguments)

        async def model_sends_message(session: FakeCopilotSession) -> None:
            await session.find_tool("band_send_message").handler(
                ToolInvocation(
                    tool_call_id="call-1",
                    tool_name="band_send_message",
                    arguments={"content": "sent via tool", "mentions": ["user-1"]},
                )
            )

        client = FakeCopilotClient(
            reply_content="Fallback reply", turn_events=[model_sends_message]
        )
        adapter = await make_started_adapter(client)
        tools = SendFailsTools()

        await run_message(adapter, tools)

        # The tool failed, so the fallback text must reach the room (no silent turn).
        assert [m["content"] for m in tools.messages_sent] == ["Fallback reply"]
