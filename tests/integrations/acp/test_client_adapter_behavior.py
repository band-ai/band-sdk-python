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

import re

import pytest

from band.integrations.acp.client_adapter import (
    HISTORY_REPLAY_HEADER,
    NEW_MESSAGE_MARKER_PREFIX,
)
from band.integrations.acp.client_types import ACPClientSessionState

from .acp_toolkit import FakeACPAgent, acp_adapter

# The header is a template ({marker} carries the per-turn nonce); its first
# line is the stable sentinel tests can look for verbatim.
REPLAY_HEADER_LINE = HISTORY_REPLAY_HEADER.splitlines()[0]
NONCED_MARKER = re.compile(rf"{re.escape(NEW_MESSAGE_MARKER_PREFIX)} [0-9a-f]{{8}}\]")


def replay_boundary(prompt: str) -> int:
    """Index of the live-message boundary marker in a replay prompt.

    Asserts the anti-spoofing contract on the way: exactly one nonce, named
    once by the header and standing once above the live message.
    """
    markers = NONCED_MARKER.findall(prompt)
    assert len(markers) == 2, f"expected header + boundary markers, got {markers}"
    assert len(set(markers)) == 1, f"header and boundary nonces differ: {markers}"
    return prompt.rindex(markers[-1])


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
async def test_band_tool_call_suppresses_text_fallback(fake_agent) -> None:
    # Detection-only: the ACP stream *reports* a completed band_send_message call,
    # but the fake doesn't actually post — will_call_tool only emits the frames — so
    # this pins the suppression decision (tool-first delivery, matching copilot_sdk /
    # codex), not the post. The end-to-end "exactly one visible reply" outcome, where
    # a real band-mcp tool posts, is covered by
    # test_band_mcp_reply_is_not_replayed_as_acp_tool_events (inject_band_tools=True).
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
    fake_agent,
) -> None:
    # Detection-only, and necessarily so: a remote band-mcp posts out-of-process, so
    # the SDK never executes the tool — detection reads the ACP tool-call stream,
    # where an MCP client prefixes the server name onto the (legacy) tool name. The
    # in-process LocalMCPServer advertises the SDK-native names, so this prefixed
    # `band-create_agent_chat_message` spelling has no real-post equivalent; this
    # test pins that is_room_posting_tool still matches it.
    fake_agent.will_call_tool(
        "tc-1", "band-create_agent_chat_message", result='{"id": "msg-1"}'
    ).will_say("Done — posted the answer.")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("question?")

    assert reply.texts == []
    assert reply.outline == ["tool_call", "tool_result", "task"]


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
async def test_streamed_tool_result_updates_collapse_into_one_event(fake_agent) -> None:
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
    assert reply.tool_results[0]["content"] == listing


@pytest.mark.asyncio
async def test_trailing_statusless_update_keeps_room_post_detected(fake_agent) -> None:
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
async def test_plan_relayed_as_task_event(fake_agent) -> None:
    fake_agent.will_plan("step one", "step two")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("make a plan")

    assert len(reply.plans) == 1
    assert "step one" in reply.plans[0] and "step two" in reply.plans[0]


@pytest.mark.asyncio
async def test_ordinary_tool_permission_granted_without_a_bubble(fake_agent) -> None:
    """An ordinary tool's permission is auto-granted silently — no permission pair.

    The tool's own tool_call/tool_result already show the call, so posting a
    permission pair too would duplicate it in the room.
    """
    fake_agent.will_ask_permission(
        tool_call_id="tc-1", title="band_lookup_peers"
    ).will_say("done")
    async with acp_adapter(fake_agent) as session:
        reply = await session.send("do the thing")

    assert fake_agent.approved is True  # the round-trip still granted the allow option
    assert reply.permissions == []  # no duplicate permission pair for an ordinary tool
    assert "done" in reply.texts  # the turn proceeded after approval


@pytest.mark.asyncio
async def test_band_mcp_reply_is_narrated_around_the_message(fake_agent) -> None:
    """A room-visible Band message still gets real ACP tool_call/tool_result
    narration, straddling the message it posts — like any other tool call."""
    fake_agent.will_call_mcp_tool(
        "tc-message",
        "band_send_message",
        arguments={
            "room_id": "room-1",
            "content": "Reply from the agent",
            "mentions": ["@pat"],
        },
    )

    async with acp_adapter(fake_agent, inject_band_tools=True) as session:
        reply = await session.send("send the reply", room="room-1")

    assert reply.outline == ["tool_call", "message", "tool_result", "task"]
    assert reply.texts == ["Reply from the agent"]
    assert len(reply.tool_calls) == 1
    assert len(reply.tool_results) == 1


@pytest.mark.asyncio
async def test_band_mcp_event_is_narrated_around_the_thought(fake_agent) -> None:
    """A room-visible Band event still gets real ACP tool_call/tool_result
    narration, straddling the event it posts — like any other tool call."""
    fake_agent.will_call_mcp_tool(
        "tc-event",
        "band_send_event",
        arguments={
            "room_id": "room-1",
            "content": "Working on it",
            "message_type": "thought",
        },
    )

    async with acp_adapter(fake_agent, inject_band_tools=True) as session:
        reply = await session.send("do the work", room="room-1")

    assert reply.outline == ["tool_call", "thought", "tool_result", "task"]
    assert reply.thoughts == ["Working on it"]
    assert len(reply.tool_calls) == 1
    assert len(reply.tool_results) == 1


@pytest.mark.asyncio
async def test_permissioned_band_mcp_turn_has_one_causal_transcript(fake_agent) -> None:
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
            "room_id": "room-1",
            "content": "Reply from the agent",
            "mentions": ["@pat"],
        },
    )

    async with acp_adapter(fake_agent, inject_band_tools=True) as session:
        reply = await session.send("send the reply", room="room-1")

    assert reply.outline == ["tool_call", "message", "tool_result", "task"]
    assert fake_agent.approved is True
    assert reply.permissions == []
    call, message, result, _task = reply.transcript
    assert call.metadata["tool_call_id"] == "tc-message"
    assert message.content == "Reply from the agent"
    assert result.metadata["tool_call_id"] == "tc-message"


@pytest.mark.asyncio
async def test_turn_events_post_in_causal_order(fake_agent) -> None:
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

    async with acp_adapter(fake_agent, inject_band_tools=True) as session:
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


# --- Band-history replay when the remote session cannot be restored ------------
#
# The remote agent owns its session state; a container restart or fresh spawn
# loses it. The Band room transcript survives on the platform, so on bootstrap
# the adapter must fall back to replaying that transcript into the new session.
# Known rehydration weaknesses guarded here: the current message must appear
# exactly once (no duplication with the replay), and it must stay the prompt's
# final word (the replay must not displace or answer over it).


def rehydration_history(
    *lines: str, session: str | None = None, room: str = "room-1"
) -> ACPClientSessionState:
    """Converted platform history handed to the adapter on bootstrap."""
    return ACPClientSessionState(
        room_to_session={room: session} if session else {},
        replay_messages=list(lines),
    )


@pytest.mark.asyncio
async def test_replay_injected_when_remote_session_is_gone() -> None:
    """session/load fails for the persisted id -> the transcript is replayed,
    framed as context, and the live message stays last and unduplicated."""
    agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
    history = rehydration_history(
        "[Marco]: My favorite color is blue.",
        "[Fake Agent]: Noted.",
        session="stale-session",
    )

    async with acp_adapter(agent) as session:
        await session.send(
            "What is my favorite color?", bootstrap=True, history=history
        )

    assert agent.session_load_requests == ["stale-session"], (
        "the persisted session must be tried before any fallback"
    )
    assert len(agent.sessions) == 1, "a failed load must fall back to a fresh session"

    prompt = agent.prompt_texts()[0]
    assert "[Marco]: My favorite color is blue." in prompt, (
        "the room transcript was not replayed into the new session's first prompt"
    )
    assert prompt.count("What is my favorite color?") == 1, (
        "the live message must appear exactly once (replay must not duplicate it)"
    )
    assert "[Peer]: What is my favorite color?" in prompt, (
        "the live message must carry sender attribution like the transcript lines"
    )
    assert (
        prompt.index("[System Context]")
        < prompt.index(REPLAY_HEADER_LINE)
        < prompt.index("[Marco]:")
        < replay_boundary(prompt)
    ), "prompt must read: system context, then replay, then the boundary marker"
    assert prompt.rstrip().endswith("What is my favorite color?"), (
        "the live message must come last so the model answers it, not the transcript"
    )


@pytest.mark.asyncio
async def test_replay_injected_when_session_load_errors() -> None:
    """A remote that errors on session/load (a protocol error, not "not found")
    must not kill the turn: the failed load counts as a miss, the transcript
    replay still fires, and the room still gets its reply."""
    agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
    agent.breaks_session_load()
    history = rehydration_history(
        "[Marco]: My favorite color is blue.", session="wedged-session"
    )

    async with acp_adapter(agent) as session:
        reply = await session.send(
            "What is my favorite color?", bootstrap=True, history=history
        )

    assert agent.session_load_requests == ["wedged-session"], (
        "the persisted session must be tried before any fallback"
    )
    assert len(agent.sessions) == 1, (
        "a load protocol error must fall back to a fresh session, not kill the turn"
    )
    assert REPLAY_HEADER_LINE in agent.prompt_texts()[0], (
        "an erroring load counts as a miss, so the replay must still fire"
    )
    assert reply.texts == ["Blue."], "the turn must complete despite the load error"


@pytest.mark.asyncio
async def test_no_replay_when_remote_session_loads() -> None:
    """A restored session already holds the conversation remotely; replaying the
    transcript on top would double the history the agent sees."""
    agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
    agent.knows_session("session-1")
    history = rehydration_history(
        "[Marco]: My favorite color is blue.", session="session-1"
    )

    async with acp_adapter(agent) as session:
        await session.send(
            "What is my favorite color?", bootstrap=True, history=history
        )

    assert agent.session_load_requests == ["session-1"]
    assert agent.sessions == [], (
        "a successful load must reuse the session, not recreate it"
    )

    prompt = agent.prompt_texts()[0]
    assert REPLAY_HEADER_LINE not in prompt and "[Marco]:" not in prompt, (
        "a restored session already holds the history remotely; replaying doubles it"
    )


@pytest.mark.asyncio
async def test_replay_injected_on_cold_boot_without_persisted_session(
    fake_agent,
) -> None:
    """No session id was ever persisted (e.g. the note landed while the agent was
    down), yet the room transcript exists: replay is the only path to context."""
    fake_agent.will_say("7421.")
    history = rehydration_history("[Marco]: The deploy code is 7421.")

    async with acp_adapter(fake_agent) as session:
        await session.send("What is the deploy code?", bootstrap=True, history=history)

    assert fake_agent.session_load_requests == [], (
        "with no persisted id there is nothing to resume; no load should be attempted"
    )
    prompt = fake_agent.prompt_texts()[0]
    assert (
        REPLAY_HEADER_LINE in prompt and "[Marco]: The deploy code is 7421." in prompt
    ), "with no session to restore, the transcript replay is the only context path"


@pytest.mark.asyncio
async def test_bootstrap_with_empty_history_has_no_replay_block(fake_agent) -> None:
    """A genuinely new room must not get an empty history frame."""
    fake_agent.will_say("Hello!")

    async with acp_adapter(fake_agent) as session:
        await session.send("hi", bootstrap=True)

    prompt = fake_agent.prompt_texts()[0]
    assert (
        REPLAY_HEADER_LINE not in prompt and NEW_MESSAGE_MARKER_PREFIX not in prompt
    ), "an empty history must not produce an empty replay frame or a stray boundary"


@pytest.mark.asyncio
async def test_replay_happens_once_not_on_later_turns(fake_agent) -> None:
    """The transcript is seeded into the session's first prompt only; repeating
    it on every turn would compound the duplication it exists to avoid."""
    fake_agent.will_say("ok")
    history = rehydration_history("[Marco]: My favorite color is blue.")

    async with acp_adapter(fake_agent) as session:
        await session.send("first question", bootstrap=True, history=history)
        await session.send("second question", history=history)

    first, second = fake_agent.prompt_texts()
    assert REPLAY_HEADER_LINE in first
    assert (
        REPLAY_HEADER_LINE not in second
        and NEW_MESSAGE_MARKER_PREFIX not in second
        and "[Marco]:" not in second
    ), "replay is seeded once per session; repeating it compounds duplication"


@pytest.mark.asyncio
async def test_replay_after_midrun_respawn() -> None:
    """A prompt failure tears the runtime down and wipes the session mappings;
    the next turn's freshly created session must be re-seeded from the room
    transcript (re-fetched, since the runtime only hands history to bootstrap
    turns), not start amnesiac."""
    from acp import RequestError

    outcomes = iter(["I noted your favorite color.", "boom", "Blue."])
    agent = FakeACPAgent()

    @agent.on_prompt
    async def _script(a: FakeACPAgent, sid: str) -> None:
        step = next(outcomes)
        if step == "boom":
            raise RequestError.internal_error()
        await a.say(sid, step)

    transcript = [
        {
            "id": "m1",
            "message_type": "text",
            "sender_name": "Marco",
            "content": "My favorite color is blue.",
        },
        {
            "id": "m2",
            "message_type": "text",
            "sender_name": "Fake Agent",
            "content": "I noted your favorite color.",
        },
    ]

    async with acp_adapter(agent) as session:
        await session.send("My favorite color is blue.", bootstrap=True)
        crashed = await session.send("anything")  # prompt raises -> adapter stop()
        reply = await session.send(
            "What is my favorite color?", room_context=transcript
        )

    assert "error" in crashed.outline, "the failed turn must surface an error event"
    assert reply.texts == ["Blue."], "the respawned turn must complete"

    prompt = agent.prompt_texts()[-1]
    assert (
        REPLAY_HEADER_LINE in prompt and "[Marco]: My favorite color is blue." in prompt
    ), "a session created after a respawn must be re-seeded from the transcript"
    assert prompt.rstrip().endswith("What is my favorite color?"), (
        "the live message must come last so the model answers it, not the transcript"
    )
