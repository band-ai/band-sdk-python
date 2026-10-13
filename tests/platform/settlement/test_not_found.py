"""Every proven 404 settles as NOT_FOUND, whatever the body looks like."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from band.platform.message_lifecycle import SettlementResult
from tests.platform.settlement.kit import Reply, SettlementPeer, Terminal, error_body


class TestNotFound:
    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(Reply(404), id="empty-body"),
            pytest.param(Reply.of_json(404, error_body("not_found")), id="valid-error"),
            pytest.param(Reply(404, "{not json"), id="malformed-json"),
            pytest.param(Reply.of_json(404, {"unexpected": 1}), id="malformed-schema"),
        ],
    )
    async def test_a_404_is_not_found(
        self, terminal: Terminal, peer: SettlementPeer, link: BandLink, reply: Reply
    ) -> None:
        peer.reply = reply

        assert await terminal.detailed(link) is SettlementResult.NOT_FOUND
