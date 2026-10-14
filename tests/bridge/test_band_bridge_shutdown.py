"""Band bridge shutdown."""

from __future__ import annotations

from bridge_core.bridge import BandBridge
from bridge_core.config import BridgeConfig

from tests.bridge.conftest import FakeForwarder, make_http_agent, make_link_mock


class TestBandBridgeShutdown:
    async def test_shutdown_closes_runners(self) -> None:
        fwd = FakeForwarder()
        link = make_link_mock()
        config = BridgeConfig(agents=[make_http_agent(agent_id="a1")])
        bridge = BandBridge(config=config, forwarders={"a1": fwd}, links={"a1": link})

        await bridge._shutdown()

        link.disconnect.assert_awaited()
        assert fwd.closed is True
