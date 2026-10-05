"""Turn-outcome probes for the opencode adapter(s); see ``turnprobes``."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from band.adapters.opencode import OpencodeAdapter
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
    event_text_part,
    wait_for,
)
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    turn_tools,
    user_message,
)
from tests.mcpbackends import backends_created_by

SESSION_ID = "sess-1"
MESSAGE_ID = "msg-1"


@contextlib.asynccontextmanager
async def started_adapter(client: FakeOpencodeClient) -> AsyncIterator[OpencodeAdapter]:
    """An adapter started against ``client``; Band MCP backend starts must
    already be faked (``backends_created_by``)."""
    adapter = OpencodeAdapter(client_factory=lambda _config: client)
    await adapter.on_started("OpenCode Agent", "A coding agent")
    try:
        yield adapter
    finally:
        await adapter.on_cleanup(ROOM_ID)


async def run_opencode(script: TurnScript, tools: FakeAgentTools) -> None:

    with backends_created_by() as starts:
        mcp_calls = BandMCPCalls(starts)
        steps = [
            event_message_updated(SESSION_ID, MESSAGE_ID),
            *(
                mcp_calls.band_tool_call(call.name, call.arguments, room_id=ROOM_ID)
                for call in script.tool_calls
            ),
        ]
        if script.final_text:
            steps.append(event_text_part(SESSION_ID, MESSAGE_ID, script.final_text))
        steps.append(event_session_idle(SESSION_ID))
        client = FakeOpencodeClient(prompt_event_sequences=[steps])

        async with started_adapter(client) as adapter:
            await adapter.on_event(turn_input(tools))


async def settle_opencode(tools: FakeAgentTools) -> None:
    """A message arriving mid-turn gets only the busy notice."""

    client = FakeOpencodeClient(
        prompt_event_sequences=[[event_message_updated(SESSION_ID, MESSAGE_ID)]]
    )
    with backends_created_by():
        async with started_adapter(client) as adapter:
            running = asyncio.create_task(adapter.on_event(turn_input(turn_tools())))
            try:
                await wait_for(lambda: bool(client.prompt_calls))
                await adapter.on_event(
                    turn_input(tools, user_message("@agent are you done?"))
                )
            finally:
                running.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await running


PROBES: dict[str, TurnOutcomeProbe] = {
    "opencode": TurnOutcomeProbe(run=run_opencode, settle=settle_opencode, relays=True),
}
