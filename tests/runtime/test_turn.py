"""The per-turn ledger: what each tool call records, and how a verdict is reported.

Recording happens on the tool methods themselves, so these tests drive real
``AgentTools`` (over the spec'd REST client) through each call path an adapter
uses: registry dispatch, a direct method call, and ``deliver_reply``.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from band.core.delivery import deliver_reply
from band.core.turn import report_unsettled_turn
from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools
from band.runtime.custom_tools import declares_turn_effect, execute_custom_tool
from band.runtime.tools import AgentTools, BandTool, TurnEffect
from band.testing.fake_tools import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)

ALICE = {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"}


@pytest.fixture
def tools(mock_rest_client: Any) -> AgentTools:
    return AgentTools("room-1", mock_rest_client, participants=[ALICE])


class LookupInput(BaseModel):
    """Look something up."""

    topic: str


class TestRecordedCallPaths:
    async def test_a_direct_send_message_is_the_reply(self, tools: AgentTools) -> None:
        await tools.send_message("hi", mentions=["@alice"])

        assert tools.turn.replied
        assert tools.turn.complete

    async def test_a_dispatched_band_no_reply_declines(self, tools: AgentTools) -> None:
        await tools.execute_tool_call(BandTool.NO_REPLY, {"reason": "FYI only"})

        assert tools.turn.replied
        assert tools.turn.complete

    async def test_deliver_reply_is_the_reply(self, tools: AgentTools) -> None:
        await deliver_reply(tools, "the answer", ["@alice"])

        assert tools.turn.replied

    async def test_real_work_completes_without_a_reply(self, tools: AgentTools) -> None:
        await tools.create_chatroom()

        assert tools.turn.complete
        assert not tools.turn.replied


class TestCallsThatRecordNothing:
    async def test_a_raising_call_records_nothing(self, tools: AgentTools) -> None:
        with pytest.raises(ValueError, match="Unknown participant"):
            await tools.send_message("hi", mentions=["@user1"])

        assert not tools.turn.replied

    async def test_a_blank_send_records_nothing(self, tools: AgentTools) -> None:
        assert await tools.send_message("  ", mentions=["@alice"]) is None

        assert not tools.turn.replied

    async def test_an_adapter_notice_is_never_the_reply(
        self, tools: AgentTools
    ) -> None:
        await tools.send_notice("Approve this tool call?", mentions=["@alice"])

        assert not tools.turn.complete

    async def test_narration_leaves_the_reply_owed(self, tools: AgentTools) -> None:
        """The nightly loss: a send to an unknown handle fails, the model
        narrates through band_send_event, then stops."""
        with pytest.raises(ValueError, match="Unknown participant"):
            await tools.send_message("hi", mentions=["@user1"])
        await tools.execute_tool_call(
            BandTool.SEND_EVENT, {"content": "retrying", "message_type": "thought"}
        )

        assert not tools.turn.complete


class TestCustomTools:
    async def test_a_declared_tool_records_its_effect(self) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def post_to_ticketing(args: LookupInput) -> str:
            return "filed"

        tools = FakeAgentTools()
        await execute_custom_tool(
            (LookupInput, post_to_ticketing), {"topic": "x"}, turn=tools.turn
        )

        assert tools.turn.complete

    async def test_an_undeclared_tool_only_observes(self) -> None:
        async def lookup(args: LookupInput) -> str:
            return "found"

        tools = FakeAgentTools()
        await execute_custom_tool(
            (LookupInput, lookup), {"topic": "x"}, turn=tools.turn
        )

        assert not tools.turn.complete

    async def test_a_failing_declared_tool_records_nothing(self) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def broken(args: LookupInput) -> str:
            raise RuntimeError("down")

        tools = FakeAgentTools()
        with pytest.raises(RuntimeError):
            await execute_custom_tool(
                (LookupInput, broken), {"topic": "x"}, turn=tools.turn
            )

        assert not tools.turn.complete


async def test_the_dedup_wrapper_shares_the_inner_ledger() -> None:
    inner = FakeAgentTools()
    wrapped = DedupingAgentTools(inner)

    await wrapped.send_message("hi", mentions=["@alice"])

    assert wrapped.turn is inner.turn
    assert inner.turn.replied


class TestReportUnsettledTurn:
    async def test_a_missing_reply_is_reported_once(self) -> None:
        tools = FakeAgentTools()

        assert await report_unsettled_turn(tools, room_id="room-1") is True
        assert await report_unsettled_turn(tools, room_id="room-1") is False

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_a_missing_reply_is_logged_for_its_room(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="band.core.turn"):
            await report_unsettled_turn(FakeAgentTools(), room_id="room-1")

        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "room-1" in record.getMessage()

    async def test_a_complete_turn_reports_nothing(self) -> None:
        tools = FakeAgentTools()
        await tools.no_reply()

        assert await report_unsettled_turn(tools, room_id="room-1") is False

        assert reported_failures(tools) == []

    async def test_a_settled_turn_reports_nothing(self) -> None:
        tools = FakeAgentTools()
        tools.turn.settle()

        assert await report_unsettled_turn(tools, room_id="room-1") is False


class TestDetach:
    def test_a_judged_turn_detaches(self) -> None:
        tools = FakeAgentTools()
        tools.turn.judged = True

        tools.turn.detach()

        assert tools.turn.detached

    def test_an_unjudged_turn_never_detaches(self) -> None:
        """A contact-hub turn parked on a decision is never reported later."""
        tools = FakeAgentTools()

        tools.turn.detach()

        assert not tools.turn.detached


async def test_a_detached_report_never_marks_the_contexts_next_message(
    mock_rest_client: Any,
) -> None:
    ctx = MagicMock(participants=[ALICE], agent_id="agent-1", hub_room_id=None)
    ctx.link.rest = mock_rest_client
    tools = AgentTools.from_context(ctx)
    tools.turn.judged = True
    tools.turn.detach()

    assert await report_unsettled_turn(tools, room_id="room-1") is True

    mock_rest_client.agent_api_events.create_agent_chat_event.assert_awaited_once()
    ctx.note_turn_failure_reported.assert_not_called()
