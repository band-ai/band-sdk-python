"""Complete successful decline batches stop the native ADK model loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import ANY

import pytest
from pydantic import create_model

from band.adapters.google_adk import GoogleADKAdapter
from band.core.types import Emit
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools.types import BandTool, TurnEffect
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.genaikit import PlatformSchemaFakeTools, text_reply
from tests.framework_conformance.turnprobes import turn_input, undeclared
from tests.integrations.googleadk.kit import (
    DECLINED,
    FOLLOWUP,
    DeclineInput,
    GatedWork,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
    result_payloads,
)


class TestDecline:
    async def test_successful_custom_decline_suppresses_the_next_model_request(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.decline_then_followup()
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 1
        assert failure_reports(tools) == []
        assert result_payloads(tools) == [
            {
                "name": "decline",
                "output": str({"result": DECLINED}),
                "tool_call_id": ANY,
                "is_error": False,
            }
        ]

    async def test_platform_decline_also_suppresses_the_next_request(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.extend(
            [batch((BandTool.NO_REPLY, {"reason": DECLINED})), text_reply(FOLLOWUP)]
        )
        adapter = GoogleADKAdapter()
        await adapter.on_started("Agent", "An agent")
        tools = PlatformSchemaFakeTools()

        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 1
        assert tools.tool_calls == [
            {"tool_name": BandTool.NO_REPLY, "arguments": {"reason": DECLINED}}
        ]
        assert failure_reports(tools) == []

    async def test_successful_decline_waits_for_every_sibling_result(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.extend(
            [batch(("decline", {}), ("work", {"value": 2})), text_reply(FOLLOWUP)]
        )
        work = GatedWork()
        adapter = GoogleADKAdapter(
            additional_tools=[(DeclineInput, decline), (WorkInput, work)]
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()
        turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await work.entered.wait()
            assert turn.done() is False
            assert len(google_model.requests) == 1
            work.release.set()
            await turn
        finally:
            work.release.set()
            await asyncio.gather(turn, return_exceptions=True)

        assert work.values == [2]
        assert len(google_model.requests) == 1
        assert result_payloads(tools) == [
            {
                "name": "decline",
                "output": str({"result": DECLINED}),
                "tool_call_id": ANY,
                "is_error": False,
            },
            {
                "name": "work",
                "output": str({"result": '{"value": 2}'}),
                "tool_call_id": ANY,
                "is_error": False,
            },
        ]

    async def test_duplicate_calls_both_execute_before_stopping(
        self, google_model: GoogleModelPeer
    ) -> None:
        calls: list[str] = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def counted_decline() -> str:
            calls.append(DECLINED)
            return DECLINED

        google_model.responses.extend(
            [batch(("decline", {}), ("decline", {})), text_reply(FOLLOWUP)]
        )
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, counted_decline)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert calls == [DECLINED, DECLINED]
        assert len(google_model.requests) == 1

    @pytest.mark.parametrize(
        "declare",
        [
            undeclared,
            declares_turn_effect(TurnEffect.OBSERVE),
            declares_turn_effect(TurnEffect.ACT),
            declares_turn_effect(TurnEffect.REPLY),
        ],
    )
    async def test_other_effects_leave_the_next_model_request_available(
        self, google_model: GoogleModelPeer, declare: Callable[..., Any]
    ) -> None:
        async def work(args: WorkInput) -> str:
            return DECLINED

        declare(work)
        google_model.responses.extend([batch(("work", {})), text_reply(FOLLOWUP)])
        adapter = GoogleADKAdapter(additional_tools=[(WorkInput, work)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_turn(
            [],
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="effects",
        )

        assert len(google_model.requests) == 2

    @pytest.mark.parametrize("emit", [set(), {Emit.TOOL_CALLS}])
    async def test_reporting_cannot_change_successful_termination(
        self, google_model: GoogleModelPeer, emit: set[Emit]
    ) -> None:
        google_model.decline_then_followup()
        adapter = GoogleADKAdapter(
            additional_tools=[(DeclineInput, decline)], emit=emit
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("Reporting unavailable")

        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 1
        assert failure_reports(tools) == []

    async def test_custom_winner_masks_the_shadowed_platform_decline_effect(
        self, google_model: GoogleModelPeer
    ) -> None:
        shadow_input = create_model(f"{BandTool.NO_REPLY}Input")
        called: list[str] = []

        async def observe() -> str:
            called.append(DECLINED)
            return DECLINED

        google_model.responses.extend(
            [batch((BandTool.NO_REPLY, {})), text_reply(FOLLOWUP)]
        )
        adapter = GoogleADKAdapter(additional_tools=[(shadow_input, observe)])
        await adapter.on_started("Agent", "An agent")
        tools = PlatformSchemaFakeTools()
        await adapter.on_turn(
            [], tools, [], None, None, is_session_bootstrap=True, room_id="shadow"
        )

        assert called == [DECLINED]
        assert tools.tool_calls == []
        assert len(google_model.requests) == 2

    async def test_legacy_act_declaration_does_not_stop_the_native_loop(
        self, google_model: GoogleModelPeer
    ) -> None:
        async def legacy() -> str:
            return DECLINED

        legacy.band_terminal = True  # type: ignore[attr-defined]
        google_model.responses.extend([batch(("decline", {})), text_reply(FOLLOWUP)])
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, legacy)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert len(google_model.requests) == 2
