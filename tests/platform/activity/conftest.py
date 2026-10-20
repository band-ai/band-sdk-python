from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from band_rest import AgentActivityDetail

import band.platform.message_lifecycle as lifecycle
from band.platform.activity import API_VERSION_HEADER, ActivitySupport
from band.platform.link import BandLink
from tests.platform.activity.kit import DETAIL, SUPPORTED_VERSION, ActivityPeer
from tests.runtime.helpers import rest_client_over


@pytest.fixture(autouse=True)
def support(monkeypatch: pytest.MonkeyPatch) -> ActivitySupport:
    support = ActivitySupport()
    monkeypatch.setattr(lifecycle, "activity_support", support)
    return support


@pytest.fixture
def activity_peer() -> ActivityPeer:
    return ActivityPeer()


@pytest.fixture
def detail() -> AgentActivityDetail:
    return AgentActivityDetail(**DETAIL)


@pytest.fixture
async def link(activity_peer: ActivityPeer) -> AsyncIterator[BandLink]:
    async with rest_client_over(activity_peer.respond) as rest:
        link = BandLink(agent_id="agent", api_key="test-key")
        link.rest = rest
        yield link


@pytest.fixture
def second_link(link: BandLink) -> BandLink:
    other = BandLink(agent_id="second-agent", api_key="test-key")
    other.rest = link.rest
    return other


@pytest.fixture
def learned(support: ActivitySupport) -> ActivitySupport:
    """A process that already saw a supporting ``X-API-Version``."""
    support.observe({API_VERSION_HEADER: SUPPORTED_VERSION})
    return support
