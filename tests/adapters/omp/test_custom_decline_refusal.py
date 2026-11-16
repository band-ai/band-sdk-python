"""A custom decline ends a native OMP turn: the model's later Band calls do nothing."""

from __future__ import annotations

from band_sdk_core import TurnVerdict
from pydantic import JsonValue

from band.integrations.ompwire import OmpContentKind, OmpFrameType
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.mailboxkit import peer_frames
from tests.framework_conformance.outcomeprobes import REPLY, TurnResult, run_turn
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    ToolCall,
    TurnLoop,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    turn_tools,
)
from tests.framework_conformance.turnprobes.omp import scripted_omp_adapter
from tests.runtime.customtools.kit import SKIP, SKIPPED, SkipInput, skip

SKIP_THEN_REPLY = TurnScript((ToolCall(SKIP), REPLY))


async def run_omp_with_skip(script: TurnScript, tools: FakeAgentTools) -> None:
    async with scripted_omp_adapter(
        script, additional_tools=[(SkipInput, skip)]
    ) as adapter:
        await adapter.on_event(turn_input(tools))


def host_tool_results(frames: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """The host-tool results the adapter answered the model's calls with."""
    return [frame for frame in frames if frame["type"] == OmpFrameType.HOST_TOOL_RESULT]


def host_tool_result(
    request: str, text: str, *, is_error: bool
) -> dict[str, JsonValue]:
    """The host-tool result frame answering ``request`` with ``text``."""
    return {
        "type": OmpFrameType.HOST_TOOL_RESULT,
        "id": request,
        "result": {"content": [{"type": OmpContentKind.TEXT, "text": text}]},
        "isError": is_error,
    }


class TestCustomDeclineRefusal:
    async def test_a_reply_after_a_custom_decline_posts_nothing(self) -> None:
        probe = TurnOutcomeProbe(loop=TurnLoop.PROVIDER, run=run_omp_with_skip)

        result = await run_turn(probe, SKIP_THEN_REPLY)

        assert result == TurnResult(
            TurnVerdict.Complete, messages=[], failures=[], participants_added=[]
        )

    async def test_the_model_receives_the_refusal_as_a_failed_result(self) -> None:
        async with scripted_omp_adapter(
            SKIP_THEN_REPLY, additional_tools=[(SkipInput, skip)]
        ) as adapter:
            await adapter.on_event(turn_input(turn_tools()))
            frames = await peer_frames(adapter, ROOM_ID)

        assert host_tool_results(frames) == [
            host_tool_result("request-0", SKIPPED, is_error=False),
            host_tool_result("request-1", DECLINED_TURN_REFUSAL, is_error=True),
        ]
