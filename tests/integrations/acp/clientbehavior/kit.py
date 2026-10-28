from __future__ import annotations

import re
from typing import Any

from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from band.converters.helpers import (
    HISTORY_REPLAY_HEADER,
    NEW_MESSAGE_MARKER_PREFIX,
)
from band.core.types import MessageType
from band.integrations.acp.client_adapter import SYSTEM_CONTEXT_HEADER
from band.integrations.acp.client_types import (
    ACPClientSessionState,
)
from band.runtime.prompts import (
    SYSTEM_UPDATE_PREFIX,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    TranscriptTools,
)
from tests.integrations.acp.acp_toolkit.agent import Gate
from tests.roomhistory import DEFAULT_HISTORY_INSERTED_AT

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


REV = {
    "id": "rev-1",
    "handle": "rev",
    "name": "Rev",
    "type": "Agent",
    "description": None,
}


def system_updates(prompt: str) -> list[str]:
    """First line of each ``[System]:`` update block in a prompt, in order.

    The projection roster/contacts tests assert on: which updates a turn
    carried and in what order, without reciting the blocks' bodies.
    """
    return [
        line.removeprefix(SYSTEM_UPDATE_PREFIX)
        for line in prompt.splitlines()
        if line.startswith(SYSTEM_UPDATE_PREFIX)
    ]


def first_line(text: str) -> str:
    return text.splitlines()[0]


def closes_the_prompt(prompt: str, live: str) -> bool:
    """Whether ``live`` (a ``live_line`` projection) ends the prompt.

    Suffix-based, so it stays correct for multi-line message content and an
    empty prompt — the contract is "the live message comes last", not "the
    live message is one line".
    """
    return prompt.endswith(live)


def band_mcp_url(agent: FakeACPAgent, session_id: str) -> str:
    return agent.mcp_server(session_id, BAND_MCP_SERVER_NAME).url


def rehydration_history(
    *lines: str, session: str | None = None, room: str = "room-1"
) -> ACPClientSessionState:
    """Converted platform history handed to the adapter on bootstrap."""
    return ACPClientSessionState(
        room_to_session={room: session} if session else {},
        replay_messages=list(lines),
    )


def room_text_message(
    message_id: str, *, sender_id: str, sender_type: str, sender_name: str, content: str
) -> dict[str, Any]:
    """A text message as the platform returns it in the room's context."""
    return {
        "id": message_id,
        "message_type": MessageType.TEXT,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "sender_name": sender_name,
        "content": content,
        "inserted_at": DEFAULT_HISTORY_INSERTED_AT,
    }


MARCO_SAYS = "My favorite color is blue."
# How the transcript replay renders ``marco_message``.
MARCO_REPLAY_LINE = f"[Marco]: {MARCO_SAYS}"


def marco_message() -> dict[str, Any]:
    """Marco's earlier room message, which a replay must carry."""
    return room_text_message(
        "m1",
        sender_id="user-marco",
        sender_type="User",
        sender_name="Marco",
        content=MARCO_SAYS,
    )


def seeded(prompt: str) -> bool:
    """Whether ``prompt`` carries the Band context and Marco's transcript."""
    return SYSTEM_CONTEXT_HEADER in prompt and MARCO_REPLAY_LINE in prompt


def resume_records(tools: TranscriptTools) -> dict[str, str]:
    """The room-to-session resume records ``tools`` posted."""
    return ACP_CLIENT_RESUME_RECORD.candidates(tools.events_sent)


# What an agent says when it refuses a prompt over its quota.
QUOTA_TEXT = "You have exhausted your daily quota for this model."


def codex_mcp_call(server: str, tool: str, **arguments: str) -> dict[str, Any]:
    """codex-acp's MCP tool call: a display title, the identity in ``rawInput``."""
    return {
        "title": f"mcp.{server}.{tool}",
        "raw_input": {"server": server, "tool": tool, "arguments": arguments},
    }


def antigravity_mcp_call(server: str, tool: str, **arguments: str) -> dict[str, Any]:
    """Antigravity's MCP tool call: a ``<server>_<tool>`` title, the identity in
    ``_meta.mcp``, and ``rawInput`` holding the arguments both nested and flat."""
    return {
        "title": f"{server}_{tool}",
        "raw_input": {"arguments": arguments, **arguments},
        "meta": {"mcp": {"server": server, "tool": tool}},
    }


class HeldThoughts(TranscriptTools):
    """Hold the first thought post until ``posting`` is released."""

    def __init__(self) -> None:
        super().__init__()
        self.posting = Gate()

    async def send_event(
        self, content: str, message_type: str, metadata: dict[str, Any] | None = None
    ) -> Any:
        if message_type == MessageType.THOUGHT and not self.posting.received.is_set():
            await self.posting.hold()
        return await super().send_event(content, message_type, metadata)
