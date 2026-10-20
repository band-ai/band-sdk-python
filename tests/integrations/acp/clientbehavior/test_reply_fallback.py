from __future__ import annotations

import pytest

from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)


class TestReplyFallback:
    @pytest.mark.asyncio
    async def test_agent_message_relayed_as_room_message(self, fake_agent) -> None:
        fake_agent.will_say("The weather is sunny.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("weather?", room="room-1")

        assert reply.texts == []
        assert reply.thoughts == ["The weather is sunny."]
        assert (
            len(fake_agent.prompts) == 1
        )  # the prompt really round-tripped to the agent

    @pytest.mark.asyncio
    async def test_streamed_text_deltas_become_one_message(self, fake_agent) -> None:
        # The agent streams its reply as many agent_message_chunk deltas; the adapter must
        # post ONE room message, not one per delta (which spammed the room word-by-word).
        fake_agent.will_stream("The weather ", "is ", "sunny.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("weather?")

        assert reply.texts == []
        assert reply.thoughts == ["The weather is sunny."]

    @pytest.mark.asyncio
    async def test_assistant_text_can_be_reported_as_a_thought(
        self, fake_agent
    ) -> None:
        # A closing aside the model wrote outside any Band tool reaches the room as
        # the agent's thought, not as a reply mentioning (and waking) the sender.
        fake_agent.will_say("(Waiting on the review; nothing to change.)")
        config = fake_agent_config()
        async with acp_adapter(fake_agent, config) as session:
            reply = await session.send("Correction: it is 585, not 584.")

        assert reply.texts == []
        assert reply.thoughts == ["(Waiting on the review; nothing to change.)"]

    @pytest.mark.asyncio
    async def test_band_tool_call_suppresses_text_fallback(self, fake_agent) -> None:
        # Out-of-process: an external band-mcp posts where the SDK never sees it, so
        # the turn records the completed call from the ACP stream. will_call_tool only
        # emits the frames, so this pins the suppression decision, not the post. The
        # in-process outcome, where a real band-mcp tool posts, is covered by
        # test_in_process_band_reply_suppresses_text_fallback.
        fake_agent.will_say("Posting the answer to the room now.").will_call_tool(
            "tc-1", "band_send_message", result='{"id": "msg-1"}'
        )
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        # Fallback text suppressed, but the call is narrated like any other tool.
        assert reply.texts == []
        assert reply.outline == ["tool_call", "tool_result", "task"]

    @pytest.mark.asyncio
    async def test_prefixed_legacy_band_tool_call_suppresses_text_fallback(
        self,
        fake_agent,
    ) -> None:
        # Detection-only, and necessarily so: a remote band-mcp posts out-of-process, so
        # the SDK never executes the tool — detection reads the ACP tool-call stream,
        # where an MCP client prefixes the server name onto the (legacy) tool name. The
        # in-process LocalMCPServer advertises the SDK-native names, so this prefixed
        # `band-create_agent_chat_message` spelling has no real-post equivalent; this
        # test pins that its effect still resolves to a reply.
        fake_agent.will_call_tool(
            "tc-1", "band-create_agent_chat_message", result='{"id": "msg-1"}'
        ).will_say("Done — posted the answer.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.outline == ["tool_call", "tool_result", "task"]

    @pytest.mark.asyncio
    async def test_in_process_band_reply_suppresses_text_fallback(
        self, fake_agent
    ) -> None:
        """An injected Band tool's post is the turn's one reply; the closing
        narration is not relayed after it."""
        fake_agent.will_call_mcp_tool(
            "tc-message",
            "band_send_message",
            arguments={
                "room_id": "room-1",
                "content": "Reply from the agent",
                "mentions": ["@pat"],
            },
        ).will_say("Posted the answer.")

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("question?", room="room-1")

        assert reply.texts == ["Reply from the agent"]

    @pytest.mark.asyncio
    async def test_injected_tools_never_settle_from_the_stream(
        self, fake_agent
    ) -> None:
        """With Band tools injected, only the tool that really posts settles the
        reply: a call the stream merely reports keeps the text fallback."""
        fake_agent.will_call_tool(
            "tc-1", "band_send_message", result='{"id": "msg-1"}'
        ).will_say("The answer is 42.")

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("question?", room="room-1")

        assert reply.texts == []
        assert reply.thoughts == ["The answer is 42."]

    @pytest.mark.asyncio
    async def test_text_relayed_when_band_post_failed(self, fake_agent) -> None:
        # A failed post must not suppress the text fallback, or the turn goes silent.
        fake_agent.will_call_tool(
            "tc-1", "band_send_message", result="boom", status="failed"
        ).will_say("The answer is 42.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.thoughts == ["The answer is 42."]

    @pytest.mark.asyncio
    async def test_text_relayed_alongside_non_posting_tool(self, fake_agent) -> None:
        # Ordinary (non-posting) tool use keeps the text reply flowing to the room.
        fake_agent.will_call_tool("tc-1", "get_weather", result="72F").will_say(
            "It's 72F."
        )
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("weather?")

        assert reply.texts == []
        assert reply.thoughts == ["It's 72F."]

    @pytest.mark.asyncio
    async def test_trailing_statusless_update_keeps_room_post_detected(
        self, fake_agent
    ) -> None:
        """A trailing status-less frame must not un-suppress the text fallback.

        The agent posts via ``band_send_message`` (reported ``completed``) and then
        emits one more ``tool_call_update`` with no status. Detection of the
        room-posting call rides that ``completed`` status; if a later frame regresses
        it to ``None`` the turn looks unposted and the agent's narration is relayed —
        duplicating the reply already in the room.
        """
        fake_agent.will_call_tool_then_trailing_update(
            "tc-1", "band_send_message", result='{"id": "msg-1"}'
        ).will_say("I've posted the answer to the room.")

        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?", room="room-1")

        assert reply.texts == []  # text suppressed: the room post was still detected

    @pytest.mark.asyncio
    async def test_intent_titled_no_reply_suppresses_closing_text(
        self,
        fake_agent: FakeACPAgent,
    ) -> None:
        fake_agent.will_call_mcp_tool(
            tool_call_id="tc-silent",
            tool_name="band_no_reply",
            title="Ending the turn silently",
            arguments={"room_id": "room-1", "reason": "nothing asked of me"},
        ).will_say("No action is needed, so I ended the turn without replying.")

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("fyi, no action needed", room="room-1")

        assert reply.texts == []

    @pytest.mark.asyncio
    async def test_intent_titled_send_message_is_not_duplicated_by_text(
        self,
        fake_agent: FakeACPAgent,
    ) -> None:
        answer = "The fix is ready for review."
        fake_agent.will_call_mcp_tool(
            tool_call_id="tc-message",
            tool_name="band_send_message",
            title="Sending the review handoff",
            arguments={
                "room_id": "room-1",
                "content": answer,
                "mentions": ["@pat"],
            },
        ).will_say("I sent the review handoff.")

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("please hand off", room="room-1")

        assert reply.texts == [answer]
        assert reply.thoughts == []

    @pytest.mark.asyncio
    async def test_intent_titled_read_only_tool_keeps_text_reply(
        self,
        fake_agent: FakeACPAgent,
    ) -> None:
        answer = "I checked the room roster."
        fake_agent.will_call_mcp_tool(
            tool_call_id="tc-roster",
            tool_name="band_get_participants",
            title="Checking who is in the room",
            arguments={"room_id": "room-1"},
        ).will_say(answer)

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("who is here?", room="room-1")

        assert reply.texts == []
        assert reply.thoughts == [answer]
