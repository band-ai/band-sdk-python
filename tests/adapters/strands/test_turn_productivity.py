from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from tests.framework_conformance.turnprobes import turn_input

pytest.importorskip("strands", reason="strands extra not installed")


from strands import tool as strands_tool
from strands.types.exceptions import EventLoopException

from band.adapters.strands import (
    StrandsAdapter,
)
from band.converters.strands import StrandsHistoryConverter
from band.core.protocols import (
    TurnResultAlreadyReported,
)
from band.core.types import (
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
    SYNTHETIC_CONTACT_EVENTS_SENDER_NAME,
    SYNTHETIC_SENDER_TYPE,
    Capability,
    Emit,
    PlatformMessage,
)
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import (
    MISSING_REPLY_FAILURE,
    ErrorTurn,
    FakeAgentTools,
    TextTurn,
    ToolTurn,
    failure_reports,
)
from tests.adapters.strands.kit import (
    ROOM,
    SEND_TURN,
    _run_message,
    _run_turn,
    _tool_results,
    failure_diagnostics,
)
from tests.framework_conformance.turnprobes import CUSTOM_TOOL_DECLARATIONS


class TestTurnProductivity:
    """A turn that reached the room ends quietly; anything else is reported."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("final_text", ["Board checked", ""])
    async def test_missing_reply_logs_reads_and_final_output_without_events(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
        final_text: str,
    ) -> None:
        adapter = await scripted(
            ToolTurn("band_list_tasks"),
            TextTurn(final_text),
            capabilities={Capability.TASKS},
            emit=set(),
        )

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        (diagnostic,) = failure_diagnostics(caplog)
        assert diagnostic["final_text"].strip() == final_text
        assert diagnostic["stop_reason"] == "end_turn"
        (call,) = diagnostic["tool_calls"]
        assert call["name"] == "band_list_tasks"
        assert call["args"] == {}
        assert call["output"]["data"] == []
        assert call["is_error"] is False
        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_failed_send_diagnostics_redact_arguments_result_and_final_text(
        self,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class FailingTools(FakeAgentTools):
            async def send_message(
                self, content: str, mentions: list[str] | None = None
            ) -> None:
                raise RuntimeError("backend down\nAuthorization: Bearer result-secret")

        tools = FailingTools(room_id=ROOM)
        adapter = await scripted(
            ToolTurn(
                "band_send_message",
                {
                    "content": "api_key=argument-secret",
                    "mentions": ["@tester"],
                },
            ),
            TextTurn("password=final-secret"),
            emit=set(),
        )

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        (diagnostic,) = failure_diagnostics(caplog)
        (call,) = diagnostic["tool_calls"]
        assert call["name"] == "band_send_message"
        assert call["is_error"] is True
        assert "backend down" in call["output"]
        assert diagnostic["final_text"].strip() == "password=[REDACTED]"
        assert call["args"]["content"] == "api_key=[REDACTED]"
        serialized = json.dumps(diagnostic)
        for secret in ("argument-secret", "result-secret", "final-secret"):
            assert secret not in serialized

    @pytest.mark.asyncio
    async def test_completed_turn_does_not_log_diagnostics(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = await scripted(SEND_TURN)
        with caplog.at_level(logging.WARNING):
            await _run_turn(adapter, tools)
        assert failure_diagnostics(caplog) == []

    @pytest.mark.asyncio
    async def test_contact_hub_turn_without_reply_does_not_log_diagnostics(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Hub-room contact events are unjudged; no reply is not a failed turn."""
        adapter = await scripted(
            ToolTurn("band_list_tasks"),
            TextTurn("noted"),
            capabilities={Capability.TASKS},
            emit=set(),
        )
        hub_msg = PlatformMessage(
            id="msg-hub",
            room_id=ROOM,
            content="contact request arrived",
            sender_id=SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
            sender_type=SYNTHETIC_SENDER_TYPE,
            sender_name=SYNTHETIC_CONTACT_EVENTS_SENDER_NAME,
            message_type="text",
            metadata=None,
            created_at=datetime.now(UTC),
        )

        with caplog.at_level(logging.WARNING):
            await adapter.on_event(turn_input(tools, msg=hub_msg))

        assert failure_diagnostics(caplog) == []
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    async def test_failure_diagnostics_preserve_redacted_structured_tool_results(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class InspectInput(BaseModel):
            """Inspect upstream state."""

            note: str

        async def inspect(args: InspectInput) -> dict[str, Any]:
            return {"state": "ready", "upstream": {"api_key": "result-secret"}}

        adapter = await scripted(
            ToolTurn("inspect", {"note": "check"}),
            TextTurn(""),
            additional_tools=[(InspectInput, inspect)],
            emit=set(),
        )

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        (diagnostic,) = failure_diagnostics(caplog)
        (call,) = diagnostic["tool_calls"]
        assert call["output"] == {
            "state": "ready",
            "upstream": {"api_key": "[REDACTED]"},
        }

    @pytest.mark.asyncio
    async def test_failure_diagnostics_only_include_the_failing_turn(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = await scripted(
            SEND_TURN,
            TextTurn("sent"),
            ToolTurn("band_lookup_peers"),
            TextTurn("checked"),
        )
        await _run_turn(adapter, tools)

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, FakeAgentTools(room_id=ROOM))

        (diagnostic,) = failure_diagnostics(caplog)
        (call,) = diagnostic["tool_calls"]
        assert call["name"] == "band_lookup_peers"
        assert diagnostic["final_text"].strip() == "checked"

    @pytest.mark.asyncio
    async def test_failure_diagnostics_keep_repeated_calls(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = await scripted(
            ToolTurn("band_list_tasks", {"limit": 1}),
            ToolTurn("band_list_tasks", {"limit": 2}),
            TextTurn(""),
            capabilities={Capability.TASKS},
            emit=set(),
        )
        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)
        (diagnostic,) = failure_diagnostics(caplog)
        first, second = diagnostic["tool_calls"]
        assert first["args"] == {"limit": 1}
        assert second["args"] == {"limit": 2}
        assert first["output"]["metadata"]["limit"] == 1
        assert second["output"]["metadata"]["limit"] == 2

    @pytest.mark.asyncio
    async def test_provider_failure_keeps_the_partial_tool_trace(
        self,
        tools: FakeAgentTools,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = await scripted(
            ToolTurn("band_list_tasks"),
            ErrorTurn(RuntimeError("provider down")),
            capabilities={Capability.TASKS},
            emit=set(),
        )
        with pytest.raises(EventLoopException, match="provider down"):
            await _run_turn(adapter, tools)
        (diagnostic,) = failure_diagnostics(caplog)
        (call,) = diagnostic["tool_calls"]
        assert call["name"] == "band_list_tasks"
        assert call["is_error"] is False
        assert diagnostic["final_text"] is None
        assert diagnostic["stop_reason"] is None

    @pytest.mark.asyncio
    async def test_failed_band_tool_is_not_terminal(self, scripted):
        """A platform tool that raised did no productive work, however it is reported."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        tools = FailingTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN)

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        # The shared bridge returns a normalized, model-visible tool failure.
        assert any(
            text.startswith("Error executing band_send_message:")
            for text in _tool_results(adapter)
        )

    @pytest.mark.asyncio
    async def test_a_failure_replays_as_a_failure_after_a_restart(self, scripted):
        """The persisted event is all a restart has; without is_error the
        converter would tell the model the failed operation succeeded."""

        class FailingTools(FakeAgentTools):
            async def send_message(self, content, mentions=None):
                raise RuntimeError("backend down")

        tools = FailingTools(room_id=ROOM)
        adapter = await scripted(SEND_TURN, emit=Emit.TOOL_CALLS)

        await _run_message(adapter, tools)

        rehydrated = StrandsHistoryConverter(agent_name="Bot").convert(
            [
                {"role": "assistant", "content": event["content"], **event}
                for event in tools.events_sent
                if event["message_type"] in ("tool_call", "tool_result")
            ]
        )
        results = [
            block["toolResult"]
            for message in rehydrated
            for block in message["content"]
            if "toolResult" in block
        ]
        assert [result["status"] for result in results] == ["error"]

    @pytest.mark.asyncio
    async def test_read_only_tool_alone_does_not_end_the_turn(self, tools, scripted):
        """Looking peers up succeeds but posts nothing, so the reply is still missing."""
        adapter = await scripted(ToolTurn("band_lookup_peers", {}))

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        assert _tool_results(adapter)  # the lookup did run and succeed
        assert tools.messages_sent == []
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_declared_native_tool_completes_the_turn(self, tools, scripted):
        """A native Strands tool runs outside the portable custom-tool path, so its
        declared effect still has to reach the turn."""

        @declares_turn_effect(TurnEffect.ACT)
        @strands_tool
        def native_finish(note: str) -> str:
            """Finish the task natively."""
            return "done"

        adapter = await scripted(
            ToolTurn("native_finish", {"note": "go"}), additional_tools=[native_finish]
        )

        await _run_turn(adapter, tools)

        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "output",
        [
            {"ok": False, "error": "upstream refused"},
            "Error: upstream refused",
            "Error executing ticket: upstream refused",
        ],
    )
    async def test_a_native_tool_failure_value_leaves_the_turn_unanswered(
        self, tools, scripted, output: Any
    ) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        @strands_tool
        def native_finish(note: str) -> Any:
            """Finish the task natively."""
            return output

        adapter = await scripted(
            ToolTurn("native_finish", {"note": "go"}), additional_tools=[native_finish]
        )

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_native_decline_alias_does_not_stop_the_reply(
        self, tools: FakeAgentTools, scripted
    ) -> None:
        @strands_tool(name="band-band_no_reply")
        def native_peek(note: str) -> str:
            """Observe a note."""
            return "seen"

        adapter = await scripted(
            ToolTurn(native_peek.tool_name, {"note": "go"}),
            SEND_TURN,
            TextTurn(""),
            additional_tools=[native_peek],
        )

        await _run_turn(adapter, tools)

        tools.assert_message_sent(content="hi", count=1)
        assert tools.turn.complete
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    async def test_undeclared_native_tool_only_observes(self, tools, scripted):
        @strands_tool
        def native_peek(note: str) -> str:
            """Peek at something."""
            return "seen"

        adapter = await scripted(
            ToolTurn("native_peek", {"note": "go"}), additional_tools=[native_peek]
        )

        with pytest.raises(TurnResultAlreadyReported):
            await _run_turn(adapter, tools)

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_invalid_tool_arguments_are_answered_not_raised(
        self, tools, scripted
    ):
        """A malformed call is the model's mistake to correct, not a turn-ending crash."""
        adapter = await scripted(ToolTurn("band_send_message", {"mentions": ["@x"]}))

        await _run_message(adapter, tools)

        assert tools.messages_sent == []
        assert _tool_results(adapter) == [
            "Invalid arguments for band_send_message: content: Field required"
        ]

    @pytest.mark.asyncio
    async def test_custom_tool_failure_is_reported_to_the_model(self, tools, scripted):
        class BoomInput(BaseModel):
            """Explode on demand."""

            note: str

        async def boom(args: BoomInput) -> str:
            raise RuntimeError("no network")

        adapter = await scripted(
            ToolTurn("boom", {"note": "go"}), additional_tools=[(BoomInput, boom)]
        )

        await _run_message(adapter, tools)

        assert _tool_results(adapter) == ["Error executing tool 'boom': no network"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, tools, scripted, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        async def file_report(args: FileInput) -> str:
            return "filed"

        adapter = await scripted(
            ToolTurn("file", {"note": "go"}),
            additional_tools=[(FileInput, file_report)],
        )

        await _run_message(adapter, tools)

        assert tools.turn.complete is complete
