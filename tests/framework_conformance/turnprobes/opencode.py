"""Turn-outcome probes for the opencode adapter(s); see ``turnprobes``."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from unittest.mock import patch

from band.adapters.opencode import OpencodeAdapter
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    turn_tools,
    user_message,
)

if TYPE_CHECKING:
    from tests.adapters.opencode.helpers import FakeMCPBackend, FakeOpencodeClient

SESSION_ID = "sess-1"
MESSAGE_ID = "msg-1"


@contextlib.asynccontextmanager
async def started_adapter(
    client: FakeOpencodeClient, backend: FakeMCPBackend
) -> AsyncIterator[OpencodeAdapter]:
    from tests.adapters.opencode.helpers import (  # noqa: PLC0415 -- the helpers import the mcp package (opencode extra) at their own top level; not installed in every lane's venv
        make_fake_mcp_backend_factory,
    )

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        make_fake_mcp_backend_factory(backend),
    ):
        adapter = OpencodeAdapter(client_factory=lambda _config: client)
        await adapter.on_started("OpenCode Agent", "A coding agent")
        try:
            yield adapter
        finally:
            await adapter.on_cleanup(ROOM_ID)


async def run_opencode(script: TurnScript, tools: FakeAgentTools) -> None:
    from tests.adapters.opencode.helpers import (  # noqa: PLC0415 -- the helpers import the mcp package (opencode extra) at their own top level; not installed in every lane's venv
        FakeMCPBackend,
        FakeOpencodeClient,
        event_message_updated,
        event_session_idle,
        event_text_part,
    )

    backend = FakeMCPBackend()
    steps = [
        event_message_updated(SESSION_ID, MESSAGE_ID),
        *(
            backend.band_tool_call(call.name, call.arguments, room_id=ROOM_ID)
            for call in script.tool_calls
        ),
    ]
    if script.final_text:
        steps.append(event_text_part(SESSION_ID, MESSAGE_ID, script.final_text))
    steps.append(event_session_idle(SESSION_ID))
    client = FakeOpencodeClient(prompt_event_sequences=[steps])

    async with started_adapter(client, backend) as adapter:
        await adapter.on_event(turn_input(tools))


async def settle_opencode(tools: FakeAgentTools) -> None:
    """A message arriving mid-turn gets only the busy notice."""
    from tests.adapters.opencode.helpers import (  # noqa: PLC0415 -- the helpers import the mcp package (opencode extra) at their own top level; not installed in every lane's venv
        FakeMCPBackend,
        FakeOpencodeClient,
        event_message_updated,
        wait_for,
    )

    client = FakeOpencodeClient(
        prompt_event_sequences=[[event_message_updated(SESSION_ID, MESSAGE_ID)]]
    )
    async with started_adapter(client, FakeMCPBackend()) as adapter:
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
