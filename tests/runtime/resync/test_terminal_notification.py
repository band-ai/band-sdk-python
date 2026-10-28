"""Terminal authority events must reach the initiator through both transports."""

from __future__ import annotations

import random
from typing import Any

import pytest

from band.client.streaming import MessageCreatedPayload
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.execution import TurnOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over, run_through
from tests.runtime.resync.kit import ReconciledRoom, ReconciliationPeer

MESSAGE_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"


def terminal_row(peer: Any) -> dict[str, Any]:
    peer.add_message(MESSAGE_ID)
    row = peer.messages[-1]
    row.update(
        sender_id=AGENT_ID,
        sender_type="Agent",
        message_type="system",
        content="Automated contract deadline: The session ended: timed_out.",
        metadata={
            "initiated_by": "system",
            "mentions": [{"id": AGENT_ID, "kind": "mention"}],
            "contract_terminal": {
                "profile": "band-contract/terminal/1",
                "message": MESSAGE_ID,
                "session": SESSION_ID,
                "state": "timed_out",
                "outcome": "timed_out",
                "version": 2,
            },
        },
    )
    return row


class TestTerminalNotification:
    @pytest.mark.parametrize("transport", ["websocket", "backlog"])
    @pytest.mark.parametrize("outcome", ["timed_out", None])
    async def test_terminal_event_invokes_real_handler_and_settles_once(
        self, reconciled_room: ReconciledRoom, transport: str, outcome: str | None
    ) -> None:
        ctx, peer, invoked = reconciled_room
        row = terminal_row(peer)
        row["metadata"]["contract_terminal"]["outcome"] = outcome
        if transport == "websocket":
            event = MessageEvent(
                room_id=ROOM_ID, payload=MessageCreatedPayload.model_validate(row)
            )
            assert await ctx._process_event(event) == TurnOutcome.ADVANCED
            assert await ctx._process_event(event) == TurnOutcome.ADVANCED
        else:
            assert await ctx.request_resync() is None
            await ctx._reconcile()
        assert invoked == [MESSAGE_ID]
        assert peer.marked("processed") == [MESSAGE_ID]
        assert peer.posts == []

    @pytest.mark.parametrize("transport", ["websocket", "backlog"])
    async def test_terminal_event_reaches_the_agent_adapter(
        self, monkeypatch: pytest.MonkeyPatch, transport: str
    ) -> None:
        monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
        peer = ReconciliationPeer()
        row = terminal_row(peer)
        adapter = ApprovalAdapter()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False, enable_working_state=False
                ),
            )
            try:
                if transport == "websocket":
                    event = MessageEvent(
                        room_id=ROOM_ID,
                        payload=MessageCreatedPayload.model_validate(row),
                    )
                    assert await ctx._process_event(event) == TurnOutcome.ADVANCED
                else:
                    assert await ctx.request_resync() is None
                    await ctx._reconcile()
            finally:
                await ctx.stop()
        assert adapter.completed == [MESSAGE_ID]
        assert peer.marked("processed") == [MESSAGE_ID]
        assert peer.posts == []

    @pytest.mark.parametrize(
        "tamper",
        [
            "text",
            "ordinary_system",
            "wrong_profile",
            "wrong_message",
            "no_recipient",
            "reference",
            "origin",
            "boolean_version",
            "extra_field",
        ],
    )
    async def test_ordinary_self_echo_and_forged_metadata_stay_silent(
        self, reconciled_room: ReconciledRoom, tamper: str
    ) -> None:
        ctx, peer, invoked = reconciled_room
        row = terminal_row(peer)
        match tamper:
            case "text":
                row["message_type"] = "text"
            case "ordinary_system":
                row["metadata"] = {}
            case "wrong_profile":
                row["metadata"]["contract_terminal"]["profile"] = "other"
            case "wrong_message":
                row["metadata"]["contract_terminal"]["message"] = SESSION_ID
            case "no_recipient":
                row["metadata"]["mentions"] = []
            case "reference":
                row["metadata"]["mentions"][0]["kind"] = "reference"
            case "origin":
                row["metadata"].pop("initiated_by")
            case "boolean_version":
                row["metadata"]["contract_terminal"]["version"] = True
            case "extra_field":
                row["metadata"]["contract_terminal"]["accepted"] = True
        event = MessageEvent(
            room_id=ROOM_ID, payload=MessageCreatedPayload.model_validate(row)
        )
        assert await ctx._process_event(event) == TurnOutcome.ADVANCED
        message = await ctx.link.get_next_message(ROOM_ID)
        assert message is not None
        assert ctx._backlog_is_nonrunnable(message)
        assert await ctx._process_backlog_message(message) == TurnOutcome.ADVANCED
        assert invoked == []
        assert peer.accepted_marks == []
        assert peer.posts == []
