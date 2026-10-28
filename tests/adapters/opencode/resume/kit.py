"""Room history, turns and prompt readings for the OpenCode resume tests."""

from __future__ import annotations

from typing import Any

from band.adapters.opencode import OpencodeAdapter
from band.converters.helpers import build_replay_messages
from band.converters.opencode import OPENCODE_RESUME_RECORD
from band.core.types import AgentInput, HistoryProvider
from band.runtime.sessionresume import SessionSeed
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    RawOpencodeEvent,
    event_message_updated,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)

ROOM_ID = "room-1"
#: The room's text history before the turns under test.
TRANSCRIPT: list[dict[str, Any]] = [
    {
        "id": "msg-earlier",
        "message_type": "text",
        "content": "Remember the code word is heron.",
        "sender_id": "user-1",
        "sender_name": "Alice",
        "sender_type": "User",
    }
]
REPLAYED = build_replay_messages(TRANSCRIPT)


async def started(adapter: OpencodeAdapter) -> OpencodeAdapter:
    """``adapter`` once the runtime has started it."""
    await adapter.on_started("OpenCode Agent", "A coding agent")
    return adapter


def room_tools() -> FakeAgentTools:
    """One turn's tools in a room whose stored messages are ``TRANSCRIPT``."""
    return FakeAgentTools(participants=ROOM_MEMBERS, room_context=TRANSCRIPT)


def room_input(
    tools: FakeAgentTools, *, history: list[dict[str, Any]], bootstrap: bool
) -> AgentInput:
    """What the runtime hands ``on_event``; history only comes on bootstrap."""
    return AgentInput(
        msg=make_platform_message(room_id=ROOM_ID, content="What is the code word?"),
        tools=tools_protocol(tools),
        history=HistoryProvider(raw=history if bootstrap else []),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=ROOM_ID,
    )


async def recorded(session: str) -> list[dict[str, Any]]:
    """The room history a confirmed seed of ``session`` left behind."""
    recorder = FakeAgentTools()
    await OPENCODE_RESUME_RECORD.post(
        recorder, room_id=ROOM_ID, seed=SessionSeed(seeded=True), session=session
    )
    return [*TRANSCRIPT, *recorder.events_sent]


def answer(session: str, message_id: str) -> list[RawOpencodeEvent]:
    """The model answers in ``session``, then the session goes idle."""
    return [
        event_message_updated(session, message_id),
        event_text_part(session, message_id, "heron"),
        event_session_idle(session),
    ]


def prompts(client: FakeOpencodeClient) -> list[tuple[str, bool]]:
    """Each prompt's session, and whether it replayed ``TRANSCRIPT``."""
    return [
        (call["session_id"], all(line in call["parts"][0]["text"] for line in REPLAYED))
        for call in client.prompt_calls
    ]
