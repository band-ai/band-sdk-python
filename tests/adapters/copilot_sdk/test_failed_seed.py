"""After a failed turn, only a seeded session is resumed; an unseeded one starts over."""

from __future__ import annotations

import pytest

from band.converters.copilot_sdk import COPILOT_RESUME_RECORD
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

REPLAY_HEADER = "[Previous conversation context:]"


def room_with_earlier_message() -> ToolSchemaFakeTools:
    tools = ToolSchemaFakeTools()
    tools.set_room_context(
        [
            {
                "id": "earlier",
                "message_type": "text",
                "sender_id": "user-1",
                "sender_type": "User",
                "sender_name": "Alice",
                "content": "my name is Alice",
            }
        ]
    )
    return tools


class TestFailedSeed:
    async def test_a_failed_first_prompt_leaves_a_fresh_replayed_session_next(self):
        client = FakeCopilotClient(prompt_errors=[RuntimeError("model unavailable")])
        adapter = await make_started_adapter(client)
        tools = room_with_earlier_message()

        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools)
        await run_message(adapter, tools, is_session_bootstrap=False)

        failed, fresh = client.sessions
        assert client.resume_calls == []
        assert fresh.session_id != failed.session_id
        assert f"{REPLAY_HEADER}\n[Alice]: my name is Alice" in fresh.prompts[0]
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": fresh.session_id
        }

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
        assert REPLAY_HEADER not in resumed.prompts[0]
