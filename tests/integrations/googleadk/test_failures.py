"""Failed ADK calls retain error identity and allow native model recovery."""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY

import pytest
from pydantic import BaseModel

from band.adapters.google_adk import GoogleADKAdapter
from band.core.exceptions import RoomClosingError
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools.types import BandTool, TurnEffect
from band.testing import FakeAgentTools, failure_reports
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.googleadk.kit import (
    DECLINED,
    DeclineInput,
    FailedPlatformTools,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
    model_results,
    result_payloads,
)


class TestFailures:
    @pytest.mark.parametrize(
        "failed", [{"ok": False, "reason": "Unavailable"}, "Error: unavailable"]
    )
    async def test_failed_decline_output_is_visible_before_successful_recovery(
        self, google_model: GoogleModelPeer, failed: Any
    ) -> None:
        outputs = iter([failed, DECLINED])

        @declares_turn_effect(TurnEffect.DECLINE)
        async def recover() -> Any:
            return next(outputs)

        google_model.responses.extend([batch(("decline", {})), batch(("decline", {}))])
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, recover)])
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()
        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 2
        assert model_results(google_model.requests[1]) == [
            ("decline", {"ok": False, "error": failed})
        ]
        assert result_payloads(tools) == [
            {
                "name": "decline",
                "output": str({"ok": False, "error": failed}),
                "tool_call_id": ANY,
                "is_error": True,
            },
            {
                "name": "decline",
                "output": str({"result": DECLINED}),
                "tool_call_id": ANY,
                "is_error": False,
            },
        ]
        assert failure_reports(tools) == []

    async def test_failed_sibling_keeps_recovery_available_after_a_decline(
        self, google_model: GoogleModelPeer
    ) -> None:
        async def fail(args: WorkInput) -> str:
            raise ValueError("Work rejected")

        google_model.responses.extend(
            [batch(("decline", {}), ("work", {})), batch(("decline", {}))]
        )
        adapter = GoogleADKAdapter(
            additional_tools=[(DeclineInput, decline), (WorkInput, fail)]
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()
        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 2
        assert model_results(google_model.requests[1]) == [
            ("decline", {"result": DECLINED}),
            ("work", {"ok": False, "error": "Work rejected"}),
        ]
        assert result_payloads(tools)[1] == {
            "name": "work",
            "output": str({"ok": False, "error": "Work rejected"}),
            "tool_call_id": ANY,
            "is_error": True,
        }

    async def test_validation_failure_does_not_execute_the_declared_handler(
        self, google_model: GoogleModelPeer
    ) -> None:
        class RequiredInput(BaseModel):
            """Require one argument before declining."""

            value: int

        executed: list[int] = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def required(args: RequiredInput) -> str:
            executed.append(args.value)
            return DECLINED

        google_model.responses.extend(
            [batch(("required", {})), batch(("required", {"value": 4}))]
        )
        adapter = GoogleADKAdapter(additional_tools=[(RequiredInput, required)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert executed == [4]
        assert len(google_model.requests) == 2
        assert model_results(google_model.requests[1]) == [
            (
                "required",
                {
                    "ok": False,
                    "error": "Invalid arguments for required: value: Field required",
                },
            )
        ]

    @pytest.mark.parametrize(
        "error", [RuntimeError("Handler unavailable"), ValueError("Handler rejected")]
    )
    async def test_exception_before_decline_success_remains_recoverable(
        self, google_model: GoogleModelPeer, error: Exception
    ) -> None:
        async def fail(args: WorkInput) -> str:
            raise error

        declares_turn_effect(TurnEffect.DECLINE)(fail)
        google_model.responses.extend([batch(("work", {})), batch(("decline", {}))])
        adapter = GoogleADKAdapter(
            additional_tools=[(WorkInput, fail), (DeclineInput, decline)]
        )
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert len(google_model.requests) == 2
        assert model_results(google_model.requests[1])[0][1]["ok"] is False

    async def test_unknown_native_sibling_is_not_hidden_by_decline(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.append(batch(("decline", {}), ("unknown_native", {})))
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")

        with pytest.raises(ValueError, match="unknown_native is not found"):
            await adapter.on_event(turn_input(FakeAgentTools()))

        assert len(google_model.requests) == 1

    async def test_lifecycle_exception_remains_a_control_signal(
        self, google_model: GoogleModelPeer
    ) -> None:
        async def closing() -> str:
            raise RoomClosingError("room-turn")

        google_model.responses.append(batch(("decline", {})))
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, closing)])
        await adapter.on_started("Agent", "An agent")

        with pytest.raises(RoomClosingError):
            await adapter.on_event(turn_input(FakeAgentTools()))

        assert len(google_model.requests) == 1

    async def test_structured_platform_failure_is_not_a_successful_decline(
        self, google_model: GoogleModelPeer
    ) -> None:
        google_model.responses.extend(
            [batch((BandTool.NO_REPLY, {"reason": DECLINED})), batch(("decline", {}))]
        )
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")
        tools = FailedPlatformTools()
        await adapter.on_event(turn_input(tools))

        assert len(google_model.requests) == 2
        assert model_results(google_model.requests[1]) == [
            (BandTool.NO_REPLY, {"ok": False, "error": "Decline rejected"})
        ]
        assert result_payloads(tools)[0] == {
            "name": BandTool.NO_REPLY,
            "output": str({"ok": False, "error": "Decline rejected"}),
            "tool_call_id": ANY,
            "is_error": True,
        }
