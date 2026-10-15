from __future__ import annotations

import pytest

from tests.integrations.acp.acp_toolkit import (
    acp_adapter,
)
from tests.integrations.acp.clientbehavior.kit import codex_mcp_call


class TestToolIdentity:
    @pytest.mark.asyncio
    async def test_prefixed_band_post_suppresses_text_and_narrates_canonically(
        self,
        fake_agent,
    ) -> None:
        """The full Copilot-shaped turn: an MCP-prefixed ``band-band_send_message``
        that completes must both suppress the text fallback (the reply is already
        in the room) and narrate under the canonical name — one vocabulary for
        suppression and narration alike."""
        fake_agent.will_call_tool(
            "tc-1", "band-band_send_message", result='{"id": "msg-1"}'
        ).will_say("Posted the answer.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.tool_call_names == ["band_send_message"]
        assert reply.tool_result_names == ["band_send_message"]
        assert reply.outline == ["tool_call", "tool_result", "task"]

    @pytest.mark.asyncio
    async def test_codex_band_post_is_the_one_reply_and_narrates_its_own_arguments(
        self,
        fake_agent,
    ) -> None:
        """codex-acp names an MCP call only in ``rawInput``; reading its display
        title instead left the reply unrecognized, so the held text posted too."""
        fake_agent.will_call_tool(
            "tc-1",
            **codex_mcp_call("band", "band_send_message", content="The answer."),
            result='{"id": "msg-1"}',
        ).will_say("The answer.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.tool_call_names == ["band_send_message"]
        assert reply.tool_call_args == [{"content": "The answer."}]

    @pytest.mark.asyncio
    async def test_codex_call_to_another_servers_same_named_tool_is_not_a_reply(
        self,
        fake_agent,
    ) -> None:
        fake_agent.will_call_tool(
            "tc-1",
            **codex_mcp_call("other", "band_send_message", content="elsewhere"),
            result="ok",
        ).will_say("The answer.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.thoughts == ["The answer."]
        assert reply.tool_call_names == ["other-band_send_message"]

    @pytest.mark.asyncio
    async def test_cursor_mcp_call_narrates_under_the_name_it_reports_late(
        self,
        fake_agent,
    ) -> None:
        """Cursor opens an MCP call as a nameless ``MCP: tool`` placeholder; posting
        that left Band tool calls unrecognizable in the room."""
        fake_agent.will_call_mcp_tool_named_late(
            "tc-1",
            "band",
            "band_list_contacts",
            arguments={"chat_id": "c1"},
            result="[]",
        ).will_say("No contacts.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("contacts?")

        assert reply.tool_call_names == ["band_list_contacts"]
        assert reply.tool_call_args == [{"chat_id": "c1"}]
        assert reply.tool_result_names == ["band_list_contacts"]
        assert reply.outline == ["tool_call", "tool_result", "thought", "task"]

    @pytest.mark.asyncio
    async def test_cursor_band_post_named_late_is_the_one_reply(
        self, fake_agent
    ) -> None:
        fake_agent.will_call_mcp_tool_named_late(
            "tc-1",
            "band",
            "band_send_message",
            arguments={"content": "The answer."},
            result='{"id": "msg-1"}',
        ).will_say("The answer.")
        async with acp_adapter(fake_agent) as session:
            reply = await session.send("question?")

        assert reply.texts == []
        assert reply.tool_call_names == ["band_send_message"]
