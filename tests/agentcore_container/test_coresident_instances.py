"""Same-identity AgentCore containers co-reside on one host, differing only by port."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.agentcore_container.kit import HEALTHY, INSTANCES, Instance
from tests.agentcore_container.peer import IdentityPeer
from tests.oneshot_server.kit import CREDENTIALS


class TestCoresidentInstances:
    # Guards a hung startup; three interpreter launches dominate the runtime.
    @pytest.mark.timeout(120)
    async def test_instances_start_concurrently_and_all_serve(
        self,
        identity_peer: tuple[IdentityPeer, str],
        instances: list[Instance],
    ) -> None:
        peer, _ = identity_peer

        # The peer answers no identity request until every instance has sent
        # one, so reaching "serving" proves their startups overlapped.
        await asyncio.gather(*(instance.serving() for instance in instances))
        async with httpx.AsyncClient() as client:
            pings = await asyncio.gather(
                *(client.get(instance.ping_url) for instance in instances)
            )

        assert [(ping.status_code, ping.json()) for ping in pings] == [
            (200, {"status": HEALTHY})
        ] * INSTANCES
        assert peer.api_keys == [CREDENTIALS["BAND_API_KEY"]] * INSTANCES
