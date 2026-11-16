"""Codex adapter: once the model declines, its later tool calls are refused unrun."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from band.adapters.codex.dynamictools import text_tool_result
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing.fake_tools import DISPATCHED_TOOL_RESULT
from tests.adapters.codex.kit import run_codex_turn
from tests.adapters.codexturns import tool_call_request, turn_completed
from tests.framework_conformance.outcomeprobes import DECLINE, REPLY
from tests.framework_conformance.turnprobes import ToolCall


class DeployInput(BaseModel):
    """Deploy the service."""


DEPLOY = ToolCall(get_custom_tool_name(DeployInput))


class TestDeclinedTurn:
    @pytest.mark.parametrize("later", [REPLY, DEPLOY], ids=["reply", "custom-act"])
    async def test_a_later_tool_call_is_refused_without_running(
        self, later: ToolCall
    ) -> None:
        deployed: list[DeployInput] = []

        @declares_turn_effect(TurnEffect.ACT)
        async def deploy(args: DeployInput) -> str:
            deployed.append(args)
            return "deployed"

        turn = await run_codex_turn(
            events=[
                tool_call_request(1, DECLINE.name, DECLINE.arguments),
                tool_call_request(2, later.name, later.arguments),
                turn_completed(),
            ],
            additional_tools=[(DeployInput, deploy)],
        )

        assert turn.client.responses == [
            (1, text_tool_result(json.dumps(DISPATCHED_TOOL_RESULT), success=True)),
            (2, text_tool_result(DECLINED_TURN_REFUSAL, success=False)),
        ]
        assert turn.tools.tool_calls == [
            {"tool_name": DECLINE.name, "arguments": DECLINE.arguments}
        ]
        assert deployed == []
