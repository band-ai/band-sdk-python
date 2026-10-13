"""One room message through the agent's ``respond`` method."""

from __future__ import annotations

import asyncio

import pytest
from nooa import GenerationError
from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaAdapter
from band.adapters.nooa.tools import platform_tool_spec, room_tools_class
from band.core.exceptions import BandToolError
from band.core.types import Emit
from band.runtime.tools import TOOL_DEFINITIONS, BandTool
from band.testing import FakeAgentTools, events_of_type, failure_reports
from band.testing.nooa import code_turn, result_turn, room_call, scripted_llm
from tests.adapters.nooa.kit import (
    Gate,
    SlowInput,
    delegating_room_agent,
    failing_llm,
    failing_report,
    prompt_text,
    room_message,
    run_turn,
    started_adapter,
    tool_events,
    tool_results,
)

REPLY = {"content": "Hi Alice", "mentions": ["Alice"]}


class TestRoomTurn:
    async def test_a_room_tool_call_in_generated_code_replies_to_the_room(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call("band_send_message", REPLY))
        )

        await run_turn(adapter, tools)

        assert tools.chat == ["Hi Alice"]

    async def test_tool_calls_are_reported_as_events_when_enabled(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call("band_send_message", REPLY)), emit={Emit.TOOL_CALLS}
        )

        await run_turn(adapter, tools)

        assert tool_events(tools) == [
            "tool_call band_send_message",
            "tool_result band_send_message",
        ]

    async def test_tool_calls_are_not_reported_when_disabled(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call("band_send_message", REPLY)), emit=set()
        )

        await run_turn(adapter, tools)

        assert tool_events(tools) == []

    async def test_a_failed_tool_call_raises_into_the_model_session(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(
            "try:\n"
            f"    {room_call('band_send_message', {'content': 'hi', 'mentions': []})}\n"
            "except Exception as error:\n"
            "    print(type(error).__name__, error)\n"
            f"{room_call('band_no_reply')}"
        )
        adapter = await started_adapter(llm, emit={Emit.TOOL_CALLS})

        await run_turn(adapter, tools)

        assert [result["is_error"] for result in tool_results(tools)] == [True, False]
        assert "BandToolError band_send_message failed" in prompt_text(llm)

    async def test_usage_is_summed_across_the_turns_model_calls(
        self, tools: FakeAgentTools
    ) -> None:
        llm = FakeLLMClient(
            [
                code_turn(
                    room_call("band_send_message", REPLY),
                    usage={"prompt_tokens": 100, "completion_tokens": 10},
                ),
                result_turn(usage={"prompt_tokens": 120, "completion_tokens": 5}),
            ]
        )
        adapter = await started_adapter(llm, emit={Emit.USAGE})

        await run_turn(adapter, tools)

        [usage_event] = [
            event for event in events_of_type(tools, "task") if event.get("metadata")
        ]
        assert usage_event["metadata"]["band_usage"] == {
            "input_tokens": 220,
            "output_tokens": 15,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    async def test_a_model_failure_is_reported_once_and_raised(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(failing_llm("provider down"))

        with pytest.raises(GenerationError, match="provider down"):
            await run_turn(adapter, tools)

        assert [provider for provider, _ in failure_reports(tools)] == ["nooa"]
        assert tools.chat == []

    async def test_a_failing_failure_report_keeps_the_models_error(
        self, tools: FakeAgentTools
    ) -> None:
        tools.send_failure = failing_report  # type: ignore[method-assign]
        adapter = await started_adapter(failing_llm("provider down"))

        with pytest.raises(GenerationError, match="provider down"):
            await run_turn(adapter, tools)

    async def test_a_tool_called_outside_a_turn_is_refused(self) -> None:
        spec = platform_tool_spec(TOOL_DEFINITIONS[BandTool.NO_REPLY])
        room = room_tools_class([spec])(emit_tool_calls=False)

        with pytest.raises(BandToolError, match="only available while"):
            await getattr(room, BandTool.NO_REPLY)()

    async def test_work_left_running_by_one_turn_cannot_act_in_the_next(self) -> None:
        llm = FakeLLMClient(
            [
                code_turn(
                    "async def later():\n"
                    "    await asyncio.sleep(0.05)\n"
                    f"    {room_call('band_send_message', {'content': 'late', 'mentions': ['Alice']})}\n"
                    "leftover = asyncio.create_task(later())\n"
                    f"{room_call('band_no_reply')}"
                ),
                result_turn(),
                code_turn(f"await asyncio.sleep(0.2)\n{room_call('band_no_reply')}"),
                result_turn(),
            ]
        )
        adapter = await started_adapter(llm)
        second_turn = FakeAgentTools()

        await run_turn(adapter, FakeAgentTools(), message=room_message("first"))
        await run_turn(
            adapter,
            second_turn,
            message=room_message("second"),
            is_session_bootstrap=False,
        )

        assert second_turn.chat == []

    async def test_a_delegating_turn_reports_its_child_agents_usage(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = NooaAdapter(agent=delegating_room_agent(), emit={Emit.USAGE})
        await adapter.on_started("Agent", "A test agent")

        await run_turn(adapter, tools)

        [usage_event] = [
            event for event in events_of_type(tools, "task") if event.get("metadata")
        ]
        assert usage_event["metadata"]["band_usage"] == {
            "input_tokens": 100,
            "output_tokens": 15,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    async def test_a_tool_call_still_running_when_the_turn_ends_is_cancelled(
        self, tools: FakeAgentTools
    ) -> None:
        gate = Gate()
        llm = scripted_llm(
            f"pending = asyncio.create_task({room_call('slow')[len('await ') :]})\n"
            "await asyncio.sleep(0.05)\n"
            f"{room_call('band_no_reply')}"
        )
        adapter = await started_adapter(
            llm, additional_tools=[(SlowInput, gate.handler)], emit={Emit.TOOL_CALLS}
        )

        await run_turn(adapter, tools)
        gate.opened.set()
        await asyncio.sleep(0.05)

        assert gate.entered.is_set()
        assert gate.committed == []
        assert {
            result["name"]: result["is_error"] for result in tool_results(tools)
        } == {"slow": True, "band_no_reply": False}

    async def test_reasoning_is_posted_as_thoughts_when_enabled(
        self, tools: FakeAgentTools
    ) -> None:
        llm = FakeLLMClient(
            [
                code_turn(room_call("band_no_reply"), reasoning="Alice only said hi."),
                result_turn(),
            ]
        )
        adapter = await started_adapter(llm, emit={Emit.THOUGHTS})

        await run_turn(adapter, tools)

        assert [event["content"] for event in events_of_type(tools, "thought")] == [
            "Alice only said hi."
        ]

    async def test_reasoning_stays_out_of_the_room_without_thoughts(
        self, tools: FakeAgentTools
    ) -> None:
        llm = FakeLLMClient(
            [
                code_turn(room_call("band_no_reply"), reasoning="Alice only said hi."),
                result_turn(),
            ]
        )
        adapter = await started_adapter(llm, emit={Emit.TOOL_CALLS})

        await run_turn(adapter, tools)

        assert events_of_type(tools, "thought") == []
