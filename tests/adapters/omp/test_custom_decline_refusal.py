"""A custom decline ends a native OMP turn: the model's later Band calls do nothing."""

from __future__ import annotations

from band_sdk_core import TurnVerdict
from pydantic import JsonValue

from band.integrations.ompwire import (
    OmpContent,
    OmpContentKind,
    OmpFrameType,
    OmpHostToolResult,
    OmpToolResult,
)
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.mailboxkit import peer_frames
from tests.framework_conformance.outcomeprobes import REPLY, TurnResult, run_turn
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    ToolCall,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    turn_tools,
)
from tests.framework_conformance.turnprobes.omp import scripted_omp_adapter
from tests.framework_conformance.turnprobes.omppeer import host_request_id
from tests.framework_conformance.turnprobes.turnloop import TurnLoop
from tests.runtime.customtools.kit import SKIP, SKIPPED, SkipInput, skip

SKIP_THEN_REPLY = TurnScript((ToolCall(SKIP), REPLY))


async def run_omp_with_skip(script: TurnScript, tools: FakeAgentTools) -> None:
    async with scripted_omp_adapter(
        script, additional_tools=[(SkipInput, skip)]
    ) as adapter:
        await adapter.on_event(turn_input(tools))


def host_tool_results(frames: list[dict[str, JsonValue]]) -> list[OmpHostToolResult]:
    """The host-tool results the adapter answered the model's calls with."""
    return [
        OmpHostToolResult.model_validate(frame)
        for frame in frames
        if frame["type"] == OmpFrameType.HOST_TOOL_RESULT
    ]


def text_result(call: int, text: str, *, is_error: bool) -> OmpHostToolResult:
    """The host-tool result answering the script's ``call``-th call with ``text``."""
    return OmpHostToolResult.from_tool(
        host_request_id(call),
        OmpToolResult(
            content=[OmpContent(type=OmpContentKind.TEXT, text=text)],
            is_error=is_error,
        ),
    )


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
            text_result(0, SKIPPED, is_error=False),
            text_result(1, DECLINED_TURN_REFUSAL, is_error=True),
        ]
