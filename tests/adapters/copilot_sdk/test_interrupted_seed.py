"""A turn interrupted while seeding: only a completed seed counts."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.copilot_sdk import HISTORY_HEADING
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from band.core.types import Emit
from tests.adapters.copilot_sdk.fakes import (
    EARLIER_LINE,
    FakeCopilotClient,
    PromptHold,
    interrupt_when,
    make_started_adapter,
    requires_copilot_sdk,
    room_with_earlier_message,
    run_message,
    usage_data,
)

pytestmark = requires_copilot_sdk

REPLAYED = f"{HISTORY_HEADING}\n{EARLIER_LINE}"


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
        assert REPLAYED in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

    async def test_an_interrupted_first_prompt_discards_its_session_and_replays(
        self,
    ):
        hold = PromptHold()
        client = FakeCopilotClient(turn_events=[hold])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        history = CopilotSDKSessionState(text=EARLIER_LINE)
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            hold.reached,
        )
        await adapter.backend_turns.settled("room-1")
        unrecorded = COPILOT_RESUME_RECORD.candidates(tools.events_sent)

        await run_message(adapter, tools, is_session_bootstrap=False)

        interrupted, fresh = client.sessions
        assert unrecorded == {}
        assert client.deleted == [interrupted.session_id]
        assert interrupted.disconnected
        assert interrupted.handlers == []
        assert client.resume_calls == []
        assert REPLAYED in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

    async def test_an_interrupted_later_prompt_keeps_the_seeded_session(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        await run_message(adapter, tools)
        (seeded,) = client.sessions
        hold = PromptHold()
        seeded.turn_events = [hold]

        await interrupt_when(
            asyncio.create_task(
                run_message(adapter, tools, is_session_bootstrap=False)
            ),
            hold.reached,
        )
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.sessions == [seeded]
        assert client.deleted == []
        assert seeded.aborted
        assert seeded.handlers == []
        assert REPLAYED not in seeded.prompts[-1]

    async def test_an_interrupt_while_dropping_a_failed_seed_still_deletes_it(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        client.hold_aborts = True
        tools = room_with_earlier_message()

        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools)), client.aborting
        )
        client.hold_aborts = False
        (failed,) = client.sessions
        failed.abort_released.set()
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.deleted == [failed.session_id]
        assert REPLAYED in client.sessions[-1].prompts[0]

    async def test_an_interrupt_after_the_seed_completed_does_not_replay_it(self):
        client = FakeCopilotClient(
            turn_events=[usage_data(input_tokens=1, output_tokens=1)]
        )
        adapter = await make_started_adapter(client, emit=Emit.USAGE)
        tools = room_with_earlier_message()
        tools.hold_usage = True
        history = CopilotSDKSessionState(text=EARLIER_LINE)
        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools, history=history)),
            tools.parked,
        )
        tools.hold_usage = False

        await run_message(adapter, tools, is_session_bootstrap=False)

        (session,) = client.sessions
        assert client.deleted == []
        assert HISTORY_HEADING not in session.prompts[1]
