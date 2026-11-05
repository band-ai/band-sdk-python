"""The native ADK model loop fails a turn that outruns its tool-round cap."""

from __future__ import annotations

import httpx
import pytest
from google.genai import types

from band.adapters.google_adk import GoogleADKAdapter, GoogleADKAdapterConfig
from band.core.adapterconfig import DEFAULT_MAX_TOOL_ROUNDS
from band.core.exceptions import ToolRoundsExceededError
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.genaikit import text_reply
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.googleadk.kit import (
    DECLINED,
    FOLLOWUP,
    DeclineInput,
    FailedPlatformTools,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
    reject,
)


class TestToolRounds:
    @pytest.mark.parametrize(
        ("config", "rounds"),
        [
            (None, DEFAULT_MAX_TOOL_ROUNDS),
            (GoogleADKAdapterConfig(max_tool_rounds=2), 2),
        ],
        ids=["default", "configured"],
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

        error = raised.value
        assert len(google_model.requests) == rounds
        assert (error.max_rounds, error.room_id, error.tool_sequence) == (
            rounds,
            ROOM_ID,
            [[BandTool.NO_REPLY]] * rounds,
        )
        assert failure_reports(tools) == [("google_adk", str(error))]
        assert closed_clients
        assert all(client.is_closed for client in closed_clients)

    async def test_each_round_records_its_whole_parallel_batch(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.extend(
            batch((BandTool.NO_REPLY, {"reason": DECLINED}), ("work", {}))
            for _ in range(3)
        )
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_tool_rounds=2),
            additional_tools=[(WorkInput, reject)],
        )
        await adapter.on_started("Agent", "An agent")

        with pytest.raises(ToolRoundsExceededError) as raised:
            await adapter.on_event(turn_input(FailedPlatformTools()))

        assert raised.value.tool_sequence == [[BandTool.NO_REPLY, "work"]] * 2

    @pytest.mark.parametrize(
        "last",
        [text_reply(FOLLOWUP), batch(("decline", {}))],
        ids=["text", "decline-bypass-uncounted"],
    )
    async def test_last_allowed_model_call_can_still_end_the_turn(
        self, google_model: GoogleModelPeer, last: types.GenerateContentResponse
    ) -> None:
        google_model.responses.extend([batch(("work", {})), last])
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_tool_rounds=2),
            additional_tools=[(WorkInput, reject), (DeclineInput, decline)],
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        await adapter.on_turn(
            [], tools, [], None, None, is_session_bootstrap=True, room_id=ROOM_ID
        )

        assert len(google_model.requests) == 2
        assert failure_reports(tools) == []

    async def test_next_turn_after_a_capped_turn_gets_a_fresh_budget(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.extend(
            [
                batch(("work", {})),
                batch(("work", {})),
                batch(("work", {})),
                text_reply(FOLLOWUP),
            ]
        )
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_tool_rounds=2),
            additional_tools=[(WorkInput, reject)],
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        with pytest.raises(ToolRoundsExceededError) as raised:
            await adapter.on_turn(
                [], tools, [], None, None, is_session_bootstrap=True, room_id=ROOM_ID
            )
        await adapter.on_turn(
            [], tools, [], None, None, is_session_bootstrap=False, room_id=ROOM_ID
        )

        assert len(google_model.requests) == 4
        assert failure_reports(tools) == [("google_adk", str(raised.value))]
