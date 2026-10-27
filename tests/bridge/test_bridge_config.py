"""BridgeConfig validates agents, ports and unknown fields."""

from __future__ import annotations

import pytest
from bridge_core.config import (
    AgentConfig,
    BridgeConfig,
    HTTPTarget,
)


class TestBridgeConfig:
    def test_basic(self) -> None:
        c = BridgeConfig(
            agents=[
                AgentConfig(
                    agent_id="a", api_key="k", target=HTTPTarget(url="https://x/y")
                )
            ]
        )
        assert len(c.agents) == 1
        assert c.health_port == 8080

    def test_rejects_empty_agents(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            BridgeConfig(agents=[])

    def test_rejects_duplicate_agent_ids(self) -> None:
        agent = AgentConfig(
            agent_id="dup", api_key="k", target=HTTPTarget(url="https://x/y")
        )
        with pytest.raises(ValueError, match="Duplicate"):
            BridgeConfig(agents=[agent, agent])

    def test_rejects_invalid_port(self) -> None:
        with pytest.raises(ValueError, match="health_port"):
            BridgeConfig(
                agents=[
                    AgentConfig(
                        agent_id="a",
                        api_key="k",
                        target=HTTPTarget(url="https://x/y"),
                    )
                ],
                health_port=99999,
            )

    def test_rejects_unknown_field_without_echoing_it(self) -> None:
        secret = "private-value"
        agent = {
            "agent_id": "a",
            "api_key": "k",
            "target": {"type": "http", "url": "https://x/y"},
        }
        with pytest.raises(ValueError, match="extra_forbidden") as error:
            BridgeConfig.model_validate({"agents": [agent], "helth_host": secret})
        assert secret not in str(error.value)
