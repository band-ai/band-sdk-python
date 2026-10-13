"""Fixtures: a scripted peer, a link over it, and both terminal writes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from band_rest.core import http_client

from band.platform.link import BandLink
from tests.platform.settlement.kit import (
    SettlementPeer,
    Terminal,
    accepted,
    link_over,
)


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the generated client's retry count but not its multi-second backoff."""
    monkeypatch.setattr(http_client, "_retry_timeout", lambda **_: 0.0)


@pytest.fixture(params=["processed", "failed"])
def terminal(request: pytest.FixtureRequest) -> Terminal:
    return Terminal(action=request.param)


@pytest.fixture
def peer(terminal: Terminal) -> SettlementPeer:
    peer = SettlementPeer()
    peer.reply = accepted(terminal.action)
    return peer


@pytest.fixture
async def link(peer: SettlementPeer) -> AsyncIterator[BandLink]:
    async with peer.client() as rest:
        yield link_over(rest)
