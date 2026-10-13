"""Usage and thought events that cover every model call a turn makes."""

from __future__ import annotations

import logging

import pytest
from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaAdapter
from band.core.types import Emit, MessageType
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, events_of_type
from band.testing.nooa import code_turn, result_turn, room_call, scripted_llm
from tests.adapters.nooa.kit import delegating_room_agent, run_turn, started_adapter
from tests.adapters.usage_events import recorded_usage_payloads

REASONED_DECLINE = [
    code_turn(room_call(BandTool.NO_REPLY), reasoning="Alice only said hi."),
    result_turn(),
]


class TestTurnTelemetry:
    async def test_usage_is_summed_across_the_turns_model_calls(
        self, tools: FakeAgentTools
    ) -> None:
        llm = FakeLLMClient(
            [
                code_turn(
                    room_call(BandTool.NO_REPLY),
                    usage={"prompt_tokens": 100, "completion_tokens": 10},
                ),
                result_turn(usage={"prompt_tokens": 120, "completion_tokens": 5}),
            ]
        )
        adapter = await started_adapter(llm, emit={Emit.USAGE})

        await run_turn(adapter, tools)

        assert recorded_usage_payloads(tools) == [
            {
                "input_tokens": 220,
                "output_tokens": 15,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ]

    async def test_a_delegating_turn_reports_its_child_agents_usage(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = NooaAdapter(agent=delegating_room_agent(), emit={Emit.USAGE})
        await adapter.on_started("Agent", "A test agent")

        await run_turn(adapter, tools)

        assert recorded_usage_payloads(tools) == [
            {
                "input_tokens": 100,
                "output_tokens": 15,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ]

    async def test_reasoning_is_posted_as_thoughts_when_enabled(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            FakeLLMClient(list(REASONED_DECLINE)), emit={Emit.THOUGHTS}
        )

        await run_turn(adapter, tools)

        assert [
            event["content"] for event in events_of_type(tools, MessageType.THOUGHT)
        ] == ["Alice only said hi."]

    async def test_reasoning_stays_out_of_the_room_without_thoughts(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            FakeLLMClient(list(REASONED_DECLINE)), emit={Emit.TOOL_CALLS}
        )

        await run_turn(adapter, tools)

        assert events_of_type(tools, MessageType.THOUGHT) == []

    async def test_the_turn_observer_answers_every_hook_nooa_calls(
        self, tools: FakeAgentTools, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = await started_adapter(scripted_llm(room_call(BandTool.NO_REPLY)))

        with caplog.at_level(logging.WARNING, logger="nooa.runtime.hooks"):
            await run_turn(adapter, tools)

        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "nooa.runtime.hooks"
        ] == []
