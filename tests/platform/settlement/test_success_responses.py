"""What a 200 response must contain to settle a terminal write."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import Reply, SettlementPeer, Terminal, accepted


class TestSuccessResponses:
    async def test_a_valid_success_payload_is_accepted(
        self, terminal: Terminal, link: BandLink
    ) -> None:
        assert await terminal.detailed(link) is SettlementResult.ACCEPTED

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(Reply(200), id="empty-body"),
            pytest.param(Reply(200, "{not json"), id="malformed-json"),
            pytest.param(Reply.of_json(200, {"unexpected": 1}), id="malformed-schema"),
        ],
    )
    async def test_an_unconfirming_payload_stays_unconfirmed(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
    ) -> None:
        settlement_peer.reply = reply

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED

    @pytest.mark.parametrize(
        ("status", "success"),
        [
            pytest.param(200, False, id="success-false"),
            pytest.param(202, True, id="non-200-status"),
        ],
    )
    async def test_an_acceptance_body_is_not_confirmed_without_a_200_success(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        status: int,
        success: bool,
    ) -> None:
        body = accepted(terminal.action, success=success).body
        settlement_peer.reply = Reply(status, body)

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
