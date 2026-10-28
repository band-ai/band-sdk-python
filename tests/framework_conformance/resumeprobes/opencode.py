"""Resume-record probe for the OpenCode adapter; see ``probe``.

The fake OpenCode server either answers the seeding prompt with a Band reply
over the adapter's MCP backend and goes idle, or rejects the prompt.
"""

from __future__ import annotations

import httpx

from band.adapters.opencode import OpencodeAdapter
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.opencode.helpers import (
    AnyHTTPStatusError,
    BandMCPCalls,
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
)
from tests.framework_conformance.outcomeprobes import REPLY
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.mcpbackends import BackendStarts, backends_created_by

# The fake server numbers the first session it creates.
SESSION_ID = "sess-1"
PROMPT_REJECTED_STATUS = 500


def seed_failure() -> type[BaseException]:
    """The error a rejected seeding prompt raises out of the turn."""
    return httpx.HTTPStatusError


def _server(outcome: SeedOutcome, starts: BackendStarts) -> FakeOpencodeClient:
    match outcome:
        case SeedOutcome.CONFIRMED:
            reply = BandMCPCalls(starts).band_tool_call(
                REPLY.name, REPLY.arguments, room_id=ROOM_ID
            )
            return FakeOpencodeClient(
                prompt_event_sequences=[
                    [
                        event_message_updated(SESSION_ID, "msg-1"),
                        reply,
                        event_session_idle(SESSION_ID),
                    ]
                ]
            )
        case SeedOutcome.FAILED:
            return FakeOpencodeClient(
                prompt_exceptions=[
                    AnyHTTPStatusError(PROMPT_REJECTED_STATUS, SESSION_ID)
                ]
            )


async def seed_opencode(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    with backends_created_by() as starts:
        client = _server(outcome, starts)
        adapter = OpencodeAdapter(client_factory=lambda _config: client, emit=())
        await adapter.on_started("OpenCode Agent", "A coding agent")
        try:
            await adapter.on_event(turn_input(tools))
        finally:
            await adapter.on_cleanup(ROOM_ID)
    [created] = client.created_sessions
    return created["id"]
