from __future__ import annotations

from agno.run.agent import RunOutput

from band.core.types import Emit


class TestEmitThoughts:
    async def test_emits_reasoning_as_thought(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(reasoning_content="thinking hard")
        adapter, _ = await make_started_adapter(response, emit=Emit.THOUGHTS)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_event_sent(message_type="thought")
        assert tools.events_sent[0]["content"] == "thinking hard"

    async def test_no_thought_without_thoughts_emit(
        self, make_started_adapter, sample_platform_message, tools
    ):
        response = RunOutput(reasoning_content="thinking hard")
        adapter, _ = await make_started_adapter(response, emit=())

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []

    async def test_no_thought_for_blank_reasoning(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(
            RunOutput(reasoning_content="  "),
            emit=Emit.THOUGHTS,
        )

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []
