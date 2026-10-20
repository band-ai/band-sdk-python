"""An error envelope settles a write only under the status that carries it."""

from __future__ import annotations

import pytest

from band.core.settlement import SettlementResult
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT_ERROR,
    NOT_FOUND_ERROR,
    Reply,
    SettlementPeer,
    Terminal,
)

# Each status paired with every envelope it does not carry.
MISMATCHES = [
    pytest.param(404, MISSING_ATTEMPT_ERROR, id="404-missing-attempt"),
    pytest.param(422, NOT_FOUND_ERROR, id="422-not-found"),
    pytest.param(409, NOT_FOUND_ERROR, id="409-not-found"),
    pytest.param(409, MISSING_ATTEMPT_ERROR, id="409-missing-attempt"),
    pytest.param(500, NOT_FOUND_ERROR, id="500-not-found"),
    pytest.param(500, MISSING_ATTEMPT_ERROR, id="500-missing-attempt"),
]


class TestStatusGating:
    @pytest.mark.parametrize(("status", "envelope"), MISMATCHES)
    async def test_an_envelope_under_the_wrong_status_is_unconfirmed(
        self,
        terminal: Terminal,
        settlement_peer: SettlementPeer,
        link: BandLink,
        status: int,
        envelope: dict[str, object],
    ) -> None:
        settlement_peer.reply = Reply.of_json(status, envelope)

        assert await terminal.detailed(link) is SettlementResult.UNCONFIRMED
