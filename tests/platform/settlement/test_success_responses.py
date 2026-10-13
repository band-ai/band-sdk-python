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
            pytest.param(accepted("processed", success=False), id="success-false"),
        ],
    )
    async def test_an_unconfirming_payload_stays_unconfirmed(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink, reply: Reply
    ) -> None:
        peer.reply = reply

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
