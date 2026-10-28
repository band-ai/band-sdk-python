"""After a failed turn, only a seeded session is resumed; an unseeded one starts over."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import HISTORY_HEADING
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from band.testing import reported_failures
from tests.adapters.copilot_sdk.fakes import (
    EARLIER_LINE,
    FakeCopilotClient,
    make_platform_message,
    make_started_adapter,
    requires_copilot_sdk,
    room_message,
    room_with_earlier_message,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestFailedSeed:
    async def test_a_failed_first_prompt_leaves_a_fresh_replayed_session_next(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        turn = make_platform_message(content="what is my name?")
        tools.append_room_context(room_message(turn.id, turn.content))

        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)
        await run_message(adapter, tools, is_session_bootstrap=False, msg=turn)

        failed, fresh = client.sessions
        assert client.resume_calls == []
        assert fresh.session_id != failed.session_id
        assert f"{HISTORY_HEADING}\n{EARLIER_LINE}" in fresh.prompts[0]
        assert "[Alice]: what is my name?" not in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

    async def test_a_failed_first_prompt_deletes_the_unseeded_session(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)

        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, room_with_earlier_message())

        assert client.deleted == [client.sessions[0].session_id]

    async def test_a_failed_delete_leaves_the_turns_own_failure_and_the_next_turn(
        self,
    ):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        client.delete_error = RuntimeError("runtime gone")
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()

        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": client.sessions[-1].session_id
        }

    @pytest.mark.looptime
    async def test_an_unanswered_delete_still_reports_the_turns_failure(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        client.hold_delete = True
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()

        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)

        assert len(reported_failures(tools)) == 1
        assert client.deleted == []

    async def test_an_unfetchable_transcript_prompts_the_fresh_session_unreplayed(
        self,
    ):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)
        tools.fetch_context_error = ConnectionError("Band REST unavailable")

        await run_message(adapter, tools, is_session_bootstrap=False)

        fresh = client.sessions[-1]
        assert HISTORY_HEADING not in fresh.prompts[0]

    async def test_a_failed_later_prompt_resumes_the_seeded_session_unreplayed(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        await run_message(adapter, tools)
        seeded = client.sessions[0]
        seeded.send_error = RuntimeError("broken pipe")

        with pytest.raises(RuntimeError, match="broken pipe"):
            await run_message(adapter, tools, is_session_bootstrap=False)
        await run_message(adapter, tools, is_session_bootstrap=False)

        resumed = client.sessions[-1]
        assert client.resume_calls == [seeded.session_id]
        assert client.deleted == []
        assert HISTORY_HEADING not in resumed.prompts[0]

    async def test_a_failed_prompt_on_a_restored_session_keeps_it_on_disk(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("broken pipe")])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()
        history = CopilotSDKSessionState(room_to_session={"room-1": "recorded"})

        with pytest.raises(RuntimeError, match="broken pipe"):
            await run_message(adapter, tools, history=history)
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.deleted == []
        assert client.resume_calls == ["recorded", "recorded"]
