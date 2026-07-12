"""Behavioral tests: ACPClientAdapter driven against an in-process fake ACP agent.

Unlike the mocked-connection tests in ``test_client_adapter.py`` (which seed the
chunk buffer directly), these run the adapter's real ``on_started`` / ``on_message``
path over a **real ACP connection** — a socketpair wiring the adapter's client to a
scripted fake agent. So genuine JSON-RPC framing, the session lifecycle, the
permission round-trip, and ``ACPCollectingClient`` chunk parsing are all exercised;
only the "LLM" is faked.

The plumbing lives in ``acp_toolkit``; the fake agent comes from the ``fake_agent``
fixture. Tests read as intent — script the agent, send a message, assert on the
:class:`Reply` (observable effects), not internals.
"""

from __future__ import annotations

import pytest

from .acp_toolkit import acp_adapter


@pytest.mark.asyncio
async def test_agent_message_relayed_as_room_message(fake_agent) -> None:
    fake_agent.will_say("The weather is sunny.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("weather?", room="room-1")

    assert reply.texts == ["The weather is sunny."]
    assert len(fake_agent.prompts) == 1  # the prompt really round-tripped to the agent


@pytest.mark.asyncio
async def test_streamed_text_deltas_become_one_message(fake_agent) -> None:
    # The agent streams its reply as many agent_message_chunk deltas; the adapter must
    # post ONE room message, not one per delta (which spammed the room word-by-word).
    fake_agent.will_stream("The weather ", "is ", "sunny.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("weather?")

    assert reply.texts == ["The weather is sunny."]


@pytest.mark.asyncio
async def test_text_suppressed_when_turn_posted_via_band_tool(fake_agent) -> None:
    # Tool-first delivery (matches copilot_sdk / codex): the agent posted its reply
    # with a Band messaging tool, so relaying its plain text too would put the
    # answer in the room twice (and leak the agent's narration of the call).
    fake_agent.will_say("Posting the answer to the room now.").will_call_tool(
        "tc-1", "band_send_message", result='{"id": "msg-1"}'
    )
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("question?")

    assert reply.texts == []
    assert len(reply.tool_calls) == 1


@pytest.mark.asyncio
async def test_text_suppressed_for_prefixed_remote_band_mcp_tool(fake_agent) -> None:
    # A remote band-mcp server posts out-of-process, so the SDK never executes the
    # tool itself; detection reads the ACP tool-call stream, where MCP clients may
    # prefix the server name onto the tool name.
    fake_agent.will_call_tool(
        "tc-1", "band-create_agent_chat_message", result='{"id": "msg-1"}'
    ).will_say("Done — posted the answer.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("question?")

    assert reply.texts == []


@pytest.mark.asyncio
async def test_text_relayed_when_band_post_failed(fake_agent) -> None:
    # A failed post must not suppress the text fallback, or the turn goes silent.
    fake_agent.will_call_tool(
        "tc-1", "band_send_message", result="boom", status="failed"
    ).will_say("The answer is 42.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("question?")

    assert reply.texts == ["The answer is 42."]


@pytest.mark.asyncio
async def test_text_relayed_alongside_non_posting_tool(fake_agent) -> None:
    # Ordinary (non-posting) tool use keeps the text reply flowing to the room.
    fake_agent.will_call_tool("tc-1", "get_weather", result="72F").will_say("It's 72F.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("weather?")

    assert reply.texts == ["It's 72F."]


@pytest.mark.asyncio
async def test_thought_relayed_as_thought_event_not_message(fake_agent) -> None:
    fake_agent.will_think("Let me think...")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("question?")

    assert reply.thoughts == ["Let me think..."]
    assert reply.texts == []  # a thought is not posted as a room message


@pytest.mark.asyncio
async def test_tool_call_and_result_relayed_as_events(fake_agent) -> None:
    fake_agent.will_call_tool(
        "tc-1", "get_weather", raw_input={"city": "SF"}, result="72F"
    )
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("weather?")

    assert len(reply.tool_calls) == 1
    assert len(reply.tool_results) == 1
    assert reply.tool_results[0]["content"] == "72F"


@pytest.mark.asyncio
async def test_plan_relayed_as_task_event(fake_agent) -> None:
    fake_agent.will_plan("step one", "step two")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("make a plan")

    assert len(reply.plans) == 1
    assert "step one" in reply.plans[0] and "step two" in reply.plans[0]


@pytest.mark.asyncio
async def test_permission_request_auto_approved(fake_agent) -> None:
    # The agent asks the client to approve a tool call mid-turn, then replies.
    fake_agent.will_ask_permission(tool_call_id="tc-1").will_say("done")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("do the thing")

    assert fake_agent.approved is True  # the round-trip granted the allow option
    assert len(reply.permissions) == 1
    assert reply.permissions[0]["metadata"]["auto_allowed"] is True
    assert "done" in reply.texts  # the turn proceeded after approval


@pytest.mark.asyncio
async def test_two_rooms_get_isolated_sessions(fake_agent) -> None:
    @fake_agent.on_prompt
    async def _reply(agent, session_id: str) -> None:
        await agent.say(session_id, f"reply for {session_id}")

    async with acp_adapter(fake_agent) as session:
        reply1 = await session.send("hi", room="room-1")
        reply2 = await session.send("hi", room="room-2")
        assert session.session_id("room-1") != session.session_id("room-2")

    # Each room created its own ACP session and got its own reply — no cross-talk.
    assert len({s["session_id"] for s in fake_agent.sessions}) == 2
    assert reply1.texts != reply2.texts
