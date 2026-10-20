"""The boolean terminal writes keep the generated client's transient retries."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from tests.platform.settlement.kit import Reply, SettlementPeer, Terminal


class TestBooleanRetry:
    @pytest.mark.parametrize("status", [408, 409, 429, 503])
    async def test_a_transient_status_is_retried_into_acceptance(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        status: int,
    ) -> None:
        settlement_peer.replies = [Reply(status)]

        assert await terminal.legacy(link) is True
        assert settlement_peer.writes == [terminal.write] * 2
