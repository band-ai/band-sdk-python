"""Fixtures: a scripted peer, a link over it, and both terminal writes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    SettlementPeer,
    Terminal,
    accepted,
    link_over,
)


@pytest.fixture(params=[DeliveryStatus.PROCESSED, DeliveryStatus.FAILED])
def terminal(request: pytest.FixtureRequest) -> Terminal:
    return Terminal(action=request.param)


@pytest.fixture
def settlement_peer(terminal: Terminal) -> SettlementPeer:
    peer = SettlementPeer()
    peer.reply = accepted(terminal.action)
    return peer


@pytest.fixture
async def link(settlement_peer: SettlementPeer) -> AsyncIterator[BandLink]:
    async with settlement_peer.client() as rest:
        yield link_over(rest)
