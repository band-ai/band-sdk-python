"""Failures that prove nothing about the message stay UNCONFIRMED."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    Reply,
    SettlementPeer,
    Terminal,
    error_body,
    link_over,
    refused_rest_client,
)


class TestUnconfirmedFailures:
    @pytest.mark.parametrize("status", [401, 403, 409, 503])
    async def test_an_error_status_is_unconfirmed(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        status: int,
    ) -> None:
        settlement_peer.reply = Reply.of_json(status, error_body("refused"))

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED

    async def test_a_retryable_status_is_not_retried_inside_one_logical_write(
        self, terminal: Terminal, settlement_peer: SettlementPeer, link: BandLink
    ) -> None:
        settlement_peer.reply = Reply(503)

        await terminal.detailed(link)

        assert settlement_peer.writes == [terminal.write]

    async def test_a_transport_failure_is_unconfirmed(self, terminal: Terminal) -> None:
        async with refused_rest_client() as rest:
            result = await terminal.detailed(link_over(rest))

        assert result is SettlementResult.UNCONFIRMED

    async def test_a_lost_success_is_not_retried_into_a_missing_attempt_refusal(
        self, terminal: Terminal, settlement_peer: SettlementPeer, link: BandLink
    ) -> None:
        settlement_peer.replies = [Reply(503), MISSING_ATTEMPT]

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
        assert settlement_peer.writes == [terminal.write]
