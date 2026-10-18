"""Only the platform error envelope proves a missing message."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import Reply, SettlementPeer, Terminal, error_body


class TestNotFound:
    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            pytest.param(Reply(404), SettlementResult.UNCONFIRMED, id="empty-body"),
            pytest.param(
                Reply.of_json(404, error_body("not_found")),
                SettlementResult.NOT_FOUND,
                id="valid-error",
            ),
            pytest.param(
                Reply.of_json(404, error_body("route_not_found")),
                SettlementResult.UNCONFIRMED,
                id="unknown-code",
            ),
            pytest.param(
                Reply(404, "{not json"),
                SettlementResult.UNCONFIRMED,
                id="malformed-json",
            ),
            pytest.param(
                Reply(404, "<html>Not found</html>"),
                SettlementResult.UNCONFIRMED,
                id="gateway-html",
            ),
            pytest.param(
                Reply.of_json(404, {"unexpected": 1}),
                SettlementResult.UNCONFIRMED,
                id="malformed-schema",
            ),
        ],
    )
    async def test_a_404_is_not_found(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        reply: Reply,
        expected: SettlementResult,
    ) -> None:
        settlement_peer.reply = reply

        assert await terminal.detailed(link) is expected
