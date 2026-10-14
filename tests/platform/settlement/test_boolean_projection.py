"""The bool methods are projections of the one detailed write."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    Reply,
    SettlementPeer,
    Terminal,
    accepted,
)

# Each outcome's reply, the result it settles to and the bool it projects to.
PROJECTIONS = {
    "accepted": (accepted(DeliveryStatus.PROCESSED), SettlementResult.ACCEPTED, True),
    "not-found": (Reply(404), SettlementResult.NOT_FOUND, False),
    "rejected": (MISSING_ATTEMPT, SettlementResult.REJECTED, False),
    "unconfirmed": (Reply(401), SettlementResult.UNCONFIRMED, False),
}


class TestBooleanProjection:
    @pytest.mark.parametrize(
        ("reply", "result", "legacy"),
        list(PROJECTIONS.values()),
        ids=list(PROJECTIONS),
    )
    async def test_each_result_projects_to_a_literal_bool(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
        result: SettlementResult,
        legacy: bool,
    ) -> None:
        settlement_peer.reply = reply

        assert await terminal.detailed(link) is result
        assert await terminal.legacy(link) is legacy

    @pytest.mark.parametrize(
        "reply",
        [reply for reply, _, _ in PROJECTIONS.values()],
        ids=list(PROJECTIONS),
    )
    async def test_each_call_is_one_logical_write(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
    ) -> None:
        settlement_peer.reply = reply

        await terminal.detailed(link)
        await terminal.legacy(link)

        assert settlement_peer.writes == [terminal.write] * 2
