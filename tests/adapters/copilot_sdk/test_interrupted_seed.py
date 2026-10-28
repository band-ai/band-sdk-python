"""A turn interrupted while seeding: the seed counts once the runtime admits it."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE, HISTORY_HEADING
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from band.core.types import Emit
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    HeldAdmission,
    HeldRestTools,
    ToolSchemaFakeTools,
    interrupt_when,
    make_started_adapter,
    requires_copilot_sdk,
    room_message,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot.generated.session_events import AssistantUsageData

ALICES_NAME = "[Alice]: my name is Alice"


def room_with_earlier_message() -> HeldRestTools:
    tools = HeldRestTools()
    tools.set_room_context([room_message("earlier", "my name is Alice")])
    return tools


class TestInterruptedSeed:
    async def test_an_interrupt_before_the_seed_is_sent_leaves_the_replay_owed(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)

        tools.hold_context = True
        interrupted = asyncio.create_task(
            run_message(adapter, tools, is_session_bootstrap=False)
        )
        await interrupt_when(interrupted, tools.parked)
        tools.hold_context = False
        await run_message(adapter, tools, is_session_bootstrap=False)

        _, fresh = client.sessions
        assert f"{HISTORY_HEADING}\n{ALICES_NAME}" in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

    async def test_an_interrupt_before_the_runtime_admits_the_seed_replays_it_again(
        self,
    ):
        client = FakeCopilotClient()
        held = client.held_admission = HeldAdmission(admits_late=False)
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        history = CopilotSDKSessionState(text=ALICES_NAME)
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            held.received,
        )
        unrecorded = COPILOT_RESUME_RECORD.candidates(tools.events_sent)

        await run_message(adapter, tools, is_session_bootstrap=False)

        (session,) = client.sessions
        assert unrecorded == {}
        assert f"{HISTORY_HEADING}\n{ALICES_NAME}" in session.prompts[1]

    async def test_an_admission_reported_after_the_interrupt_still_seeds(self):
        client = FakeCopilotClient()
        held = client.held_admission = HeldAdmission(admits_late=True)
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        history = CopilotSDKSessionState(text=ALICES_NAME)
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            held.received,
        )

        await run_message(adapter, tools, is_session_bootstrap=False)

        (session,) = client.sessions
        assert HISTORY_HEADING not in session.prompts[1]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": session.session_id
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
        history = CopilotSDKSessionState(text=ALICES_NAME)
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

    async def test_an_interrupt_after_the_seed_completed_does_not_replay_it(self):
        client = FakeCopilotClient(
            turn_events=[AssistantUsageData(model="m", input_tokens=1, output_tokens=1)]
        )
        adapter = await make_started_adapter(client, emit=Emit.USAGE)
        tools = room_with_earlier_message()
        tools.hold_usage = True
        history = CopilotSDKSessionState(text=ALICES_NAME)
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            tools.parked,
        )
        tools.hold_usage = False

        await run_message(adapter, tools, is_session_bootstrap=False)

        (session,) = client.sessions
        assert HISTORY_HEADING not in session.prompts[1]
