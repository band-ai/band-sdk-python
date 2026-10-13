"""Band bridge construction."""

from __future__ import annotations

from bridge_core.bridge import BandBridge
from bridge_core.config import BridgeConfig
from bridge_core.forwarder import Forwarder

from tests.bridge.conftest import FakeForwarder, make_http_agent, make_link_mock


class TestBandBridgeConstruction:
    def test_builds_runner_per_agent(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1", url="https://x/y"),
                make_http_agent(agent_id="a2", url="https://y/z"),
            ]
        )
        links = {"a1": make_link_mock(), "a2": make_link_mock()}
        forwarders: dict[str, Forwarder] = {
            "a1": FakeForwarder(),
            "a2": FakeForwarder(),
        }

        bridge = BandBridge(config=config, forwarders=forwarders, links=links)

        assert len(bridge.runners) == 2
        ids = {r.agent_id for r in bridge.runners}
        assert ids == {"a1", "a2"}

    def test_each_runner_gets_its_own_forwarder(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1"),
                make_http_agent(agent_id="a2"),
            ]
        )
        fwd_a = FakeForwarder()
        fwd_b = FakeForwarder()
        links = {"a1": make_link_mock(), "a2": make_link_mock()}

        bridge = BandBridge(
            config=config,
            forwarders={"a1": fwd_a, "a2": fwd_b},
            links=links,
        )

        runners_by_id = {r.agent_id: r for r in bridge.runners}
        assert runners_by_id["a1"].forwarder is fwd_a
        assert runners_by_id["a2"].forwarder is fwd_b
