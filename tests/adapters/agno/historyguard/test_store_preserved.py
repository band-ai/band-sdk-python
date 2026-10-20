from __future__ import annotations

from agno.models.message import Message
from agno.run.agent import RunOutput

from tests.adapters.agno.helpers import make_agent_input


class TestStorePreserved:
    async def test_transcript_is_still_stored_when_guard_on(
        self, make_started_adapter, sample_platform_message, tools
    ):
        turn = RunOutput(
            content="a1",
            messages=[
                Message(role="user", content="[Alice]: q1"),
                Message(role="assistant", content="a1"),
            ],
        )
        adapter, _ = await make_started_adapter(
            turn, add_history_to_context=True, db=object(), replies_through=tools
        )

        room_id = sample_platform_message.room_id
        await adapter.on_event(
            make_agent_input(
                sample_platform_message, [], is_session_bootstrap=True, tools=tools
            )
        )

        # "Store the history, just don't rehydrate it": _persist_turn still records
        # the transcript even though it is no longer fed back into the run input.
        assert adapter._message_history[room_id] == turn.messages
