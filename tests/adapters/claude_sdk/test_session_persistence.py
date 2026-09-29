from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from claude_agent_sdk import CLIConnectionError

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_room_resumes_the_session_its_history_recorded(
    claude_room: OpenRoom,
) -> None:
    """Bootstrap resumes the recorded session; the id is re-recorded once, and
    only a bootstrap ever asks to resume."""
    room = await claude_room()
    room.claude.script([room.model_reply("welcome back")], [room.model_reply("ok")])

    await room.send("hello again", session_id="sess-from-history")
    await room.send("next", session_id="sess-should-not-use")

    assert room.claude.resumed == ["sess-from-history"]
    assert room.persisted_sessions == ["sess-from-history"]
    assert room.chat == ["welcome back", "ok"]


async def test_a_session_that_cannot_resume_falls_back_to_a_fresh_one(
    claude_room: OpenRoom,
) -> None:
    """A recorded session the CLI can no longer resume heals silently: a fresh
    session answers, and its id replaces the stale one."""
    room = await claude_room()
    room.claude.unresumable.add("sess-broken")
    room.claude.script([room.model_reply("fresh start")])

    await room.send("hello", session_id="sess-broken")

    assert room.claude.resumed == ["sess-broken", None]
    [fresh] = room.claude.sessions[1:]
    assert room.persisted_sessions == [fresh.session_id]
    assert room.chat == ["fresh start"]
    assert room.failures == []


@pytest.mark.parametrize(
    ("recorded_session", "attempts"),
    [(None, [None]), ("sess-broken", ["sess-broken", None])],
    ids=["no-session-to-resume", "fallback-also-fails"],
)
async def test_a_cli_that_will_not_start_fails_the_message_without_leaking_why(
    claude_room: OpenRoom, recorded_session: str | None, attempts: list[str | None]
) -> None:
    room = await claude_room()
    room.claude.refuse_connect = True

    with pytest.raises(CLIConnectionError, match="exited during startup"):
        await room.send("hello", session_id=recorded_session)

    assert room.claude.resumed == attempts
    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    assert room.chat == []


async def test_events_the_room_rejects_do_not_break_the_turn(
    claude_room: OpenRoom,
) -> None:
    """Recording the session id and narrating tool use are best-effort: with
    every event rejected, the reply still lands and the turn is not failed."""
    room = await claude_room()
    room.tools.send_event_error = RuntimeError("events endpoint down")
    room.claude.script([room.model_reply("answered anyway")])

    await room.send("hello")

    assert room.chat == ["answered anyway"]
    assert room.persisted_sessions == []
