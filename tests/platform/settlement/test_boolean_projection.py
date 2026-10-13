"""The bool methods are projections of the one detailed write."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    Reply,
    SettlementPeer,
    Terminal,
    accepted,
)

PROJECTIONS = [
    pytest.param(accepted("processed"), SettlementResult.ACCEPTED, True, id="accepted"),
    pytest.param(Reply(404), SettlementResult.NOT_FOUND, False, id="not-found"),
    pytest.param(MISSING_ATTEMPT, SettlementResult.REJECTED, False, id="rejected"),
    pytest.param(Reply(401), SettlementResult.UNCONFIRMED, False, id="unconfirmed"),
]


class TestBooleanProjection:
    @pytest.mark.parametrize(("reply", "result", "legacy"), PROJECTIONS)
    async def test_each_result_projects_to_a_literal_bool(
        self,
        terminal: Terminal,
        peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
        result: SettlementResult,
        legacy: bool,
    ) -> None:
        peer.reply = reply

        assert await terminal.detailed(link) is result
        assert await terminal.legacy(link) is legacy

    @pytest.mark.parametrize(("reply", "result", "legacy"), PROJECTIONS)
    async def test_each_call_is_one_logical_write(
        self,
        terminal: Terminal,
        peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
        result: SettlementResult,
        legacy: bool,
    ) -> None:
        peer.reply = reply

        await terminal.detailed(link)
        await terminal.legacy(link)

        assert peer.writes == [terminal.write] * 2
