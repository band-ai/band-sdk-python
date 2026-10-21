"""Regression test for the kill-shot single-identity bug: a message in a shared room must be forwardable to each agent that participates."""

from __future__ import annotations

from bridge_core.bridge import BandBridge
from bridge_core.config import BridgeConfig

from tests.bridge.conftest import FakeForwarder, make_http_agent, make_link_mock
from tests.bridge.kit import (
    make_message_event,
)


class TestBandBridgeMultiIdentityIsolation:
    """Regression test for the kill-shot single-identity bug: a message in
    a shared room must be forwardable to each agent that participates.
    """

    async def test_each_runner_forwards_independently(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1"),
                make_http_agent(agent_id="a2"),
            ]
        )
        fwd_a = FakeForwarder()
        fwd_b = FakeForwarder()
        bridge = BandBridge(
            config=config,
            forwarders={"a1": fwd_a, "a2": fwd_b},
            links={"a1": make_link_mock(), "a2": make_link_mock()},
        )

        runners_by_id = {r.agent_id: r for r in bridge.runners}

        # Simulate the same message being delivered on a2's WS subscription
        # (which would happen if a1 sent it in a room where both participate).
        msg = make_message_event(sender_id="a1", message_id="m1")
        await runners_by_id["a2"]._handle_event(msg)

        # a2's forwarder received it. The bridge does NOT filter self-messages
        # by sender_id (that's the SDK's job inside the container).
        assert len(fwd_b.forwarded) == 1
        assert fwd_b.forwarded[0]["agent_id"] == "a2"
        assert fwd_b.forwarded[0]["payload"]["sender_id"] == "a1"

        # a1's runner only sees events its own subscription delivers; in this
        # test we routed the event to a2 only, so a1's forwarder is empty.
        assert len(fwd_a.forwarded) == 0
