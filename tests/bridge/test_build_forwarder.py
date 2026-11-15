"""build_forwarder picks the forwarder matching the target type."""

from __future__ import annotations

from bridge_core.config import AgentCoreTarget, HTTPTarget
from bridge_core.forwarder import (
    AgentCoreForwarder,
    HTTPForwarder,
    build_forwarder,
)


class TestBuildForwarder:
    def test_builds_http(self, http_target: HTTPTarget) -> None:
        f = build_forwarder(http_target)
        assert isinstance(f, HTTPForwarder)

    def test_builds_agentcore(self, agentcore_target: AgentCoreTarget) -> None:
        f = build_forwarder(agentcore_target)
        assert isinstance(f, AgentCoreForwarder)
