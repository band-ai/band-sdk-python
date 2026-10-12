"""Codex adapter: native agent messages become thoughts unless a reply effect settles them."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    agent_message_completed,
    agent_message_delta,
    agent_message_started,
    tool_call_request,
    turn_completed,
)


class TestNativeMessageThoughts:
    """Native agent messages become thoughts unless a reply effect settles them."""

    @pytest.mark.parametrize("stream", [True, False])
    @pytest.mark.parametrize("emit_thoughts", [True, False])
    @pytest.mark.parametrize("completed", ["thinking", "thinking longer", "revised"])
    async def test_completed_native_items_reconcile_streamed_thoughts(
        self,
        stream: bool,
        emit_thoughts: bool,
        completed: str,
    ) -> None:
        turn = await run_codex_turn(
            events=[
                agent_message_started("comment", phase="commentary"),
                agent_message_delta("thinking", "comment"),
                agent_message_completed(completed, "comment", phase="commentary"),
                agent_message_completed("final", "answer"),
                turn_completed(),
            ],
            config=CodexAdapterConfig(stream_commentary_events=stream),
            emit={Emit.THOUGHTS} if emit_thoughts else set(),
        )
        expected: list[str] = []
        if emit_thoughts:
            expected = [completed, "final"]
            if stream:
                expected = ["thinking"]
                if completed != "thinking":
                    expected.append(
                        " longer" if completed == "thinking longer" else "revised"
                    )
                expected.append("final")
        assert [e["content"] for e in events_of_type(turn.tools, "thought")] == expected
        assert turn.tools.chat == []
        assert not turn.tools.turn.complete

    @pytest.mark.parametrize("effect", [BandTool.SEND_MESSAGE, BandTool.NO_REPLY])
    async def test_completed_native_text_waits_for_later_reply_effects(
        self, effect: str
    ) -> None:
        args = (
            {"content": "tool answer", "mentions": ["@a"]}
            if effect == BandTool.SEND_MESSAGE
            else {}
        )
        turn = await run_codex_turn(
            events=[
                agent_message_completed("early closing text"),
                tool_call_request(1, effect, args),
                turn_completed(),
            ]
        )
        assert events_of_type(turn.tools, "thought") == []
        assert turn.tools.turn.replied

    @pytest.mark.parametrize("status", ["interrupted", "failed"])
    async def test_completed_native_text_is_suppressed_after_provider_failure(
        self,
        status: str,
    ) -> None:
        tools = ToolSchemaFakeTools()
        if status == "failed":
            with pytest.raises(TurnResultAlreadyReported):
                await run_codex_turn(
                    tools=tools,
                    events=[
                        agent_message_completed("not delivered"),
                        turn_completed(status=status),
                    ],
                )
        else:
            await run_codex_turn(
                tools=tools,
                events=[
                    agent_message_completed("not delivered"),
                    turn_completed(status=status),
                ],
            )
        assert events_of_type(tools, "thought") == []

    @pytest.mark.parametrize("phase", [None, "final_answer", "commentary"])
    async def test_incomplete_native_items_have_no_closing_fallback(
        self,
        phase: str | None,
    ) -> None:
        turn = await run_codex_turn(
            events=[
                agent_message_started(phase=phase),
                agent_message_delta("partial"),
                turn_completed(),
            ]
        )
        assert turn.tools.chat == []
        assert events_of_type(turn.tools, "thought") == []
        assert not turn.tools.turn.complete

    async def test_failed_native_thought_delivery_does_not_settle_the_turn(
        self,
    ) -> None:
        tools = ToolSchemaFakeTools()
        tools.send_event_error = RuntimeError("telemetry unavailable")
        await run_codex_turn(
            tools=tools,
            events=[agent_message_completed("closing narration"), turn_completed()],
        )
        assert not tools.turn.complete
        assert tools.chat == []

    @pytest.mark.parametrize(
        "phase, include_phase",
        [(None, False), (None, True), ("final_answer", True), ("commentary", True)],
    )
    async def test_completed_native_messages_without_reply_effects_remain_thoughts(
        self,
        phase: str | None,
        include_phase: bool,
    ) -> None:
        completed = agent_message_completed("native", phase=phase)
        if not include_phase:
            completed.params["item"].pop("phase")
        turn = await run_codex_turn(events=[completed, turn_completed()])
        assert [
            event["content"] for event in events_of_type(turn.tools, "thought")
        ] == ["native"]
        assert turn.tools.chat == []
        assert not turn.tools.turn.complete
