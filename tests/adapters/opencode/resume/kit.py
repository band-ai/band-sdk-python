"""Room history, turns and prompt readings for the OpenCode resume tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from band.adapters.opencode import OpencodeAdapter
from band.converters.helpers import build_replay_messages
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
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
#: The message each turn under test answers.
QUESTION = make_platform_message(room_id=ROOM_ID, content="What is the code word?")
#: A message that reached the room after ``QUESTION``.
LATER = {
    "id": "msg-later",
    "message_type": "text",
    "content": "Never mind.",
    "sender_id": "user-1",
    "sender_name": "Alice",
    "sender_type": "User",
}


async def started(adapter: OpencodeAdapter) -> OpencodeAdapter:
    """``adapter`` once the runtime has started it."""
    await adapter.on_started("OpenCode Agent", "A coding agent")
    return adapter


def room_tools() -> FakeAgentTools:
    """One turn's tools in a room holding ``TRANSCRIPT``, ``QUESTION``, ``LATER``."""
    return FakeAgentTools(
        participants=ROOM_MEMBERS,
        room_context=[*TRANSCRIPT, _stored(QUESTION), LATER],
    )


def _stored(message: PlatformMessage) -> dict[str, Any]:
    """``message`` as the room's context stores it."""
    return {
        "id": message.id,
        "message_type": message.message_type,
        "content": message.content,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "sender_type": message.sender_type,
    }


def room_input(
    tools: FakeAgentTools, *, history: list[dict[str, Any]], bootstrap: bool
) -> AgentInput:
    """What the runtime hands ``on_event``; history only comes on bootstrap."""
    return AgentInput(
        msg=QUESTION,
        tools=tools_protocol(tools),
        history=HistoryProvider(raw=history if bootstrap else []),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=ROOM_ID,
    )


async def recorded(
    make_adapter: Callable[..., OpencodeAdapter],
) -> list[dict[str, Any]]:
    """The room history after a real turn seeded and recorded ``sess-1``."""
    client = FakeOpencodeClient(prompt_event_sequences=[answer("sess-1", "msg-seed")])
    adapter = await started(make_adapter(client, emit=()))
    tools = room_tools()
    await adapter.on_event(room_input(tools, history=TRANSCRIPT, bootstrap=True))
    return [*TRANSCRIPT, *tools.events_sent]


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
        (call["session_id"], all(line in prompt_text(call) for line in REPLAYED))
        for call in client.prompt_calls
    ]


def prompt_text(call: dict[str, Any]) -> str:
    """The text one prompt sent."""
    return call["parts"][0]["text"]
