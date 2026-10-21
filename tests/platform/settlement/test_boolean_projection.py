"""The bool methods are projections of the one detailed write."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    NOT_FOUND,
    Reply,
    SettlementPeer,
    Terminal,
)

# Each outcome's reply, the result it settles to and the bool it projects to.
# A ``None`` reply keeps the peer's accepted reply for the terminal under test.
PROJECTIONS = {
    "accepted": (None, SettlementResult.ACCEPTED, True),
    "not-found": (NOT_FOUND, SettlementResult.NOT_FOUND, False),
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
        reply: Reply | None,
        result: SettlementResult,
        legacy: bool,
    ) -> None:
        settlement_peer.reply = reply or settlement_peer.reply

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
        reply: Reply | None,
    ) -> None:
        settlement_peer.reply = reply or settlement_peer.reply

        await terminal.detailed(link)
        await terminal.legacy(link)

        assert settlement_peer.writes == [terminal.write] * 2
