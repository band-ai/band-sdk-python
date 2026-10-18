from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pytest_httpx import HTTPXMock

import band.platform.message_lifecycle as lifecycle
from band.platform.activity import ActivitySupport
from band.platform.link import BandLink
from tests.platform.activity.kit import VERSION_URL, ActivityPeer
from tests.runtime.helpers import rest_client_over


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> ActivitySupport:
    support = ActivitySupport()
    monkeypatch.setattr(lifecycle, "activity_support", support)
    return support


@pytest.fixture
async def activity_peer(
    httpx_mock: HTTPXMock, support: ActivitySupport
) -> ActivityPeer:
    activity_peer = ActivityPeer()
    httpx_mock.add_callback(
        activity_peer.discover, url=VERSION_URL, is_optional=True, is_reusable=True
    )
    return activity_peer


@pytest.fixture
async def link(activity_peer: ActivityPeer) -> AsyncIterator[BandLink]:
    async with rest_client_over(activity_peer.respond) as rest:
        link = BandLink(
            agent_id="agent", api_key="test-key", rest_url="https://platform.test"
        )
        link.rest = rest
        yield link
