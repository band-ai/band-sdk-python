"""Owned real HTTP link construction for activity observer tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
from phoenix_channels_python_client import PHXChannelsClient

from band.client.streaming import WebSocketClient
from tests.e2e.baseline.toolkit.activity import ActivityCapture, ObservingBandLink
from tests.e2e.baseline.toolkit.activitywork import ActivityWork
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.framework_conformance.activitycapture.kit import HeldActivityPeer
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer
from tests.framework_conformance.activitycapture.topics import TopicPeer
from tests.runtime.helpers import rest_client_over


@pytest.fixture
async def held_lookup() -> AsyncIterator[tuple[ActivityWork, HeldLookupPeer]]:
    peer = HeldLookupPeer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.respond)) as client:
        work = ActivityWork(client, "https://lookup.invalid", deadline_s=10)
        async with work.releasing_on_exit():
            try:
                yield work, peer
            finally:
                peer.release.set()


@pytest.fixture
async def observed_link() -> AsyncIterator[
    tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer]
]:
    capture = ActivityCapture()
    peer = HeldActivityPeer()
    async with rest_client_over(peer.respond) as rest:
        link = capture.link(agent_id="agent", api_key="test-key")
        link.rest = rest
        try:
            yield capture, link, peer
        finally:
            peer.release.set()


@pytest.fixture
def raw_peer() -> tuple[TrackingWebSocketClient, TopicPeer]:
    peer = TopicPeer()
    ws = WebSocketClient(ws_url="ws://peer.invalid", api_key="test-key")
    ws.client = cast(PHXChannelsClient, peer)
    return TrackingWebSocketClient(ws), peer
