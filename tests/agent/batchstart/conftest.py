"""A loopback platform whose REST surface is the stateful peer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from band.core.types import ConflictPolicy
from band.platform.link import BandLink
from band.testing import fake_phoenix_server
from tests.agent.batchstart.kit import AgentPeer, Platform
from tests.runtime.helpers import rest_client_over
from tests.scenarios.observations import Observations


@pytest_asyncio.fixture(loop_scope="function")
async def platform(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Platform]:
    """The socket is a real loopback server. ``PlatformRuntime`` builds its own
    link, so the link it builds gets the peer's REST client in place of one
    aimed at ``rest_url``."""
    peer = AgentPeer(Observations())
    async with fake_phoenix_server() as server, rest_client_over(peer.answer) as rest:

        def link_over_peer(
            *,
            agent_id: str,
            api_key: str,
            ws_url: str,
            rest_url: str,
            conflict_policy: ConflictPolicy,
        ) -> BandLink:
            link = BandLink(
                agent_id=agent_id,
                api_key=api_key,
                ws_url=ws_url,
                rest_url=rest_url,
                conflict_policy=conflict_policy,
            )
            link.rest = rest
            return link

        monkeypatch.setattr("band.runtime.platform_runtime.BandLink", link_over_peer)
        yield Platform(server, peer)
