"""A turn interrupted while seeding: before or after the runtime admits the seed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from tests.adapters.copilot_sdk.fakes import (
    HISTORY_HEADING,
    FakeCopilotClient,
    HeldContextTools,
    ToolSchemaFakeTools,
    interrupt_when,
    make_started_adapter,
    requires_copilot_sdk,
    room_message,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestInterruptedSeed:
    async def test_an_interrupt_before_the_seed_is_sent_leaves_the_replay_owed(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        tools = HeldContextTools()
        tools.set_room_context([room_message("earlier", "my name is Alice")])
        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)

        tools.hold = True
        interrupted = asyncio.create_task(
            run_message(adapter, tools, is_session_bootstrap=False)
        )
        await interrupt_when(interrupted, tools.fetching)
        tools.hold = False
        await run_message(adapter, tools, is_session_bootstrap=False)

        _, fresh = client.sessions
        assert f"{HISTORY_HEADING}\n[Alice]: my name is Alice" in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

    async def test_an_interrupt_after_the_seed_is_admitted_keeps_it_resumable(self):
        admitted = asyncio.Event()

        async def park_the_first_prompt(_session: Any) -> None:
            if not admitted.is_set():
                admitted.set()
                await asyncio.Event().wait()  # until the turn is interrupted

        client = FakeCopilotClient(turn_events=[park_the_first_prompt])
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        history = CopilotSDKSessionState(text="[Alice]: my name is Alice")
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            admitted,
        )
        seeded = client.sessions[0]
        seeded.send_error = RuntimeError("broken pipe")

        with pytest.raises(RuntimeError, match="broken pipe"):
            await run_message(adapter, tools, is_session_bootstrap=False)
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.resume_calls == [seeded.session_id]
        assert client.deleted == []
        assert HISTORY_HEADING not in client.sessions[-1].prompts[0]
