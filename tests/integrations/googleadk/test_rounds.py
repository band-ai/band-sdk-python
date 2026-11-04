"""The native ADK model loop fails a turn that outruns its tool-round cap."""

from __future__ import annotations

import httpx
import pytest
from google.genai import types
from pydantic import ValidationError

from band.adapters.google_adk import GoogleADKAdapter, GoogleADKAdapterConfig
from band.core.adapterconfig import DEFAULT_MAX_TOOL_ROUNDS
from band.core.exceptions import ToolRoundsExceededError
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.genaikit import text_reply
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.googleadk.kit import (
    DECLINED,
    FOLLOWUP,
    WORK_REJECTED,
    DeclineInput,
    FailedPlatformTools,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
)


class TestToolRounds:
    @pytest.mark.parametrize(
        ("config", "rounds"),
        [
            (None, DEFAULT_MAX_TOOL_ROUNDS),
            (GoogleADKAdapterConfig(max_tool_rounds=2), 2),
        ],
    )
    async def test_repeated_rejected_decline_fails_at_the_round_cap(
        self,
        google_model: GoogleModelPeer,
        closed_clients: list[httpx.AsyncClient],
        config: GoogleADKAdapterConfig | None,
        rounds: int,
    ) -> None:
        google_model.responses.extend(
            batch((BandTool.NO_REPLY, {"reason": DECLINED})) for _ in range(rounds + 1)
        )
        adapter = GoogleADKAdapter(config)
        await adapter.on_started("Agent", "An agent")
        tools = FailedPlatformTools()

        with pytest.raises(ToolRoundsExceededError) as raised:
            await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == rounds
        assert raised.value.tool_sequence == [[BandTool.NO_REPLY]] * rounds
        assert failure_reports(tools) == [("google_adk", str(raised.value))]
        assert closed_clients
        assert all(client.is_closed for client in closed_clients)

    @pytest.mark.parametrize(
        "last",
        [text_reply(FOLLOWUP), batch(("decline", {}))],
        ids=["text", "decline"],
    )
    async def test_last_allowed_model_call_can_still_end_the_turn(
        self, google_model: GoogleModelPeer, last: types.GenerateContentResponse
    ) -> None:
        async def fail(args: WorkInput) -> str:
            raise ValueError(WORK_REJECTED)

        google_model.responses.extend([batch(("work", {})), last])
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_tool_rounds=2),
            additional_tools=[(WorkInput, fail), (DeclineInput, decline)],
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        await adapter.on_turn(
            [], tools, [], None, None, is_session_bootstrap=True, room_id="last"
        )

        assert len(google_model.requests) == 2
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("max_tool_rounds", [0, -1])
    def test_round_cap_must_be_positive(self, max_tool_rounds: int) -> None:
        with pytest.raises(ValidationError, match="max_tool_rounds"):
            GoogleADKAdapterConfig(max_tool_rounds=max_tool_rounds)
