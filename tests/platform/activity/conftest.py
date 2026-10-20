from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from band_rest import AgentActivityDetail

from band.platform.activity import ActivitySupport
from band.platform.link import BandLink
from tests.activitysupport import install_activity_support
from tests.platform.activity.kit import DETAIL, ActivityPeer
from tests.runtime.helpers import rest_client_over


@pytest.fixture(autouse=True)
def support(monkeypatch: pytest.MonkeyPatch) -> ActivitySupport:
    return install_activity_support(monkeypatch, learned=False)


@pytest.fixture
def activity_peer() -> ActivityPeer:
    return ActivityPeer()


@pytest.fixture
def detail() -> AgentActivityDetail:
    return AgentActivityDetail(**DETAIL)


@pytest.fixture
async def link(activity_peer: ActivityPeer) -> AsyncIterator[BandLink]:
    """A link on the SDK's default (SaaS) ``rest_url``."""
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
def learned(monkeypatch: pytest.MonkeyPatch) -> ActivitySupport:
    """A process that already saw a supporting ``X-API-Version``."""
    return install_activity_support(monkeypatch, learned=True)
