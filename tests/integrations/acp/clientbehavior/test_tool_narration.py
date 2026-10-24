from __future__ import annotations

import pytest
from pydantic import BaseModel

from band.core.types import Capability
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)


class TestToolNarration:
    @pytest.mark.asyncio
    async def test_mcp_prefixed_band_tool_narrated_under_canonical_name(
        self,
        fake_agent,
    ) -> None:
        """Copilot registers the loopback server's tools as ``band-<tool>``; the
        narrated tool_call/tool_result must carry the canonical band name so every
        consumer matches one vocabulary."""
        fake_agent.will_call_tool(
            "tc-1",
            "band-band_create_chatroom",
            raw_input={"title": "war room"},
            result='{"id": "room-9"}',
        ).will_say("Created the room.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("make a room")

        assert reply.tool_call_names == ["band_create_chatroom"]
        assert reply.tool_result_names == ["band_create_chatroom"]

    @pytest.mark.asyncio
    async def test_prefixed_custom_tool_narrated_under_canonical_name(
        self,
    ) -> None:
        """A custom tool registered on the loopback server canonicalizes too."""

        class EchoInput(BaseModel):
            text: str

        async def echo(text: str) -> str:
            return text

        agent = FakeACPAgent()
        agent.will_call_tool(
            "tc-1", "band-echo", raw_input={"text": "hi"}, result="hi"
        ).will_say("done")
        async with acp_adapter(agent, additional_tools=[(EchoInput, echo)]) as session:
            reply = await session.send("echo hi")

        assert reply.tool_call_names == ["echo"]

    @pytest.mark.asyncio
    async def test_canonical_vocabulary_follows_declared_capabilities(
        self, fake_agent
    ) -> None:
        """A memory tool's MCP spelling canonicalizes only when the MEMORY
        capability (which registers the tool) is declared — the vocabulary and
        the registration stay one set."""
        fake_agent.will_call_tool(
            "tc-1", "band-band_store_memory", raw_input={"content": "x"}, result="ok"
        ).will_say("stored")
        async with acp_adapter(fake_agent, capabilities=Capability.MEMORY) as session:
            reply = await session.send("remember x")

        assert reply.tool_call_names == ["band_store_memory"]

    @pytest.mark.asyncio
    async def test_foreign_tool_names_pass_through_unrewritten(
        self, fake_agent
    ) -> None:
        """The agent's own (non-band) tools keep their reported names — only a
        name that reveals one of ours behind the server prefix is rewritten."""
        fake_agent.will_call_tool(
            "tc-1", "band-grep", raw_input={"pattern": "x"}, result="match"
        ).will_say("found it")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("search")

        assert reply.tool_call_names == ["band-grep"]

    @pytest.mark.asyncio
    async def test_thought_relayed_as_thought_event_not_message(
        self, fake_agent
    ) -> None:
        fake_agent.will_think("Let me think...")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.thoughts == ["Let me think..."]
        assert reply.texts == []  # a thought is not posted as a room message

    @pytest.mark.asyncio
    async def test_tool_call_and_result_relayed_as_events(self, fake_agent) -> None:
        fake_agent.will_call_tool(
            "tc-1", "get_weather", raw_input={"city": "SF"}, result="72F"
        )
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("weather?")

        assert len(reply.tool_calls) == 1
        assert len(reply.tool_results) == 1
        assert reply.outline == ["tool_call", "tool_result", "task"]

    @pytest.mark.asyncio
    async def test_terminal_tool_execution_round_trips_to_acp_editor(
        self, fake_agent, acp_editor
    ) -> None:
        """A tool run by an ACP client remains a tool lifecycle for an ACP editor."""
        room_id = "room-1"
        tool_call_id = "tc-probe"
        tool_title = "Run terminal command"
        command = "printf ACP_TOOL_PROBE"
        output = "ACP_TOOL_PROBE"
        expected_editor_updates = ["tool_call", "tool_call_update"]

        fake_agent.will_call_tool(
            tool_call_id,
            tool_title,
            raw_input={"command": command},
            result=output,
        )
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("Run the probe command.", room=room_id)

        editor = acp_editor.awaiting_reply(room_id)
        await editor.forward_tool_activity(reply)

        updates = editor.updates
        assert [update.session_update for update in updates] == expected_editor_updates
        assert updates[0].tool_call_id == tool_call_id
        assert updates[0].raw_input == {"command": command}
        assert updates[1].tool_call_id == tool_call_id
        assert updates[1].raw_output == output

    @pytest.mark.asyncio
    async def test_streamed_tool_result_updates_collapse_into_one_event(
        self, fake_agent
    ) -> None:
        """One tool call reported over several ACP updates posts exactly one tool_result.

        A real agent streams a call's result as a start plus a run of tool_call_updates
        sharing an id — readable content-block frames, then a terminal frame carrying
        only the structured ``rawOutput``. The room must see one result event with the
        readable listing, not one event per frame and not the stringified dict.
        """
        listing = "AGENTS.md\nCHANGELOG.md\nsrc\ntests"
        fake_agent.will_stream_tool_result(
            "tc-ls",
            "List repository root files",
            text=listing,
            raw_output={"content": listing, "shellId": 0, "exitCode": 0},
        )

        async with acp_adapter(fake_agent) as session:
            reply = await session.send("run ls", room="room-1")

        assert len(reply.tool_results) == 1
        assert reply.outline == ["tool_call", "tool_result", "task"]

    @pytest.mark.asyncio
    async def test_plan_relayed_as_task_event(self, fake_agent) -> None:
        fake_agent.will_plan("step one", "step two")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("make a plan")

        assert len(reply.plans) == 1
        assert "step one" in reply.plans[0] and "step two" in reply.plans[0]

    @pytest.mark.asyncio
    async def test_band_mcp_reply_is_narrated_around_the_message(
        self, fake_agent
    ) -> None:
        """A room-visible Band message still gets real ACP tool_call/tool_result
        narration, straddling the message it posts — like any other tool call."""
        fake_agent.will_call_mcp_tool(
            "tc-message",
            "band_send_message",
            arguments={
                "content": "Reply from the agent",
                "mentions": ["@pat"],
            },
        )

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("send the reply", room="room-1")

        assert reply.outline == ["tool_call", "message", "tool_result", "task"]
        assert reply.texts == ["Reply from the agent"]
        assert len(reply.tool_calls) == 1
        assert len(reply.tool_results) == 1

    @pytest.mark.asyncio
    async def test_band_mcp_event_is_narrated_around_the_thought(
        self, fake_agent
    ) -> None:
        """A room-visible Band event still gets real ACP tool_call/tool_result
        narration, straddling the event it posts — like any other tool call."""
        fake_agent.will_call_mcp_tool(
            "tc-event",
            "band_send_event",
            arguments={
                "content": "Working on it",
                "message_type": "thought",
            },
        )

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("do the work", room="room-1")

        assert reply.outline == ["tool_call", "thought", "tool_result", "task"]
        assert reply.thoughts == ["Working on it"]
        assert len(reply.tool_calls) == 1
        assert len(reply.tool_results) == 1

    @pytest.mark.asyncio
    async def test_permissioned_band_mcp_turn_has_one_causal_transcript(
        self, fake_agent
    ) -> None:
        """Permission, tool execution, and visible reply must retain causal order.

        The permission grants silently (no synthetic pair — see
        test_permission_handler_skips_pair_for_approved_band_send_message); the
        call's own real tool_call/tool_result narration straddles the message
        instead, so the room reads the same call -> reply -> result shape.
        """
        fake_agent.will_ask_permission(
            tool_call_id="tc-message",
            title="band_send_message",
        ).will_call_mcp_tool(
            "tc-message",
            "band_send_message",
            arguments={
                "content": "Reply from the agent",
                "mentions": ["@pat"],
            },
        )

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("send the reply", room="room-1")

        assert reply.outline == ["tool_call", "message", "tool_result", "task"]
        assert fake_agent.approved is True
        assert reply.permissions == []
        call, message, result, _task = reply.transcript
        assert call.metadata["tool_call_id"] == "tc-message"
        assert message.content == "Reply from the agent"
        assert result.metadata["tool_call_id"] == "tc-message"

    @pytest.mark.asyncio
    async def test_turn_events_post_in_causal_order(self, fake_agent) -> None:
        """Narration and the in-room reply keep the order they happened.

        The reply-before-narration bug: a Band messaging tool posts to the room as it
        runs (mid-turn), but narration used to be flushed only after the whole turn —
        so it landed after the live events instead of where it happened. A turn that
        thinks, calls an ordinary tool, then posts via band_send_message must render
        thought → tool call/result (get_weather) → tool call → room message → tool
        result → task, in that order (narration is not trailing at the end, and
        band_send_message's own tool_call/tool_result straddle the message it posted,
        same as any other tool call). The permission grants silently — see
        test_permissioned_band_mcp_turn_has_one_causal_transcript.
        """
        fake_agent.will_think("Checking the weather first.").will_call_tool(
            "tc-weather", "get_weather", raw_input={"city": "SF"}, result="72F"
        ).will_ask_permission(
            tool_call_id="tc-msg", title="band_send_message"
        ).will_call_mcp_tool(
            "tc-msg",
            "band_send_message",
            arguments={
                "room_id": "room-1",
                "content": "It's 72F in SF.",
                "mentions": ["@pat"],
            },
        )

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            reply = await session.send("weather in SF?", room="room-1")

        assert reply.outline == [
            "thought",
            "tool_call",  # the ordinary get_weather tool
            "tool_result",
            "tool_call",  # band_send_message's own call, asked before posting the reply
            "message",  # the band_send_message post
            "tool_result",  # band_send_message's own result, lands after the message
            "task",  # session bookkeeping, always last
        ]
        assert reply.texts == ["It's 72F in SF."]
