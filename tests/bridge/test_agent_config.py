"""AgentConfig validates identity fields and rejects unknown options without echoing them."""

from __future__ import annotations

import pytest
from bridge_core.config import (
    AgentConfig,
    AgentCoreTarget,
    HTTPTarget,
)


class TestAgentConfig:
    def test_http_target(self) -> None:
        a = AgentConfig(
            agent_id="aid",
            api_key="key",
            target=HTTPTarget(url="https://x/y"),
        )
        assert isinstance(a.target, HTTPTarget)

    def test_agentcore_target(self) -> None:
        a = AgentConfig(
            agent_id="aid",
            api_key="key",
            target=AgentCoreTarget(runtime_arn="arn:x", region="us-east-1"),
        )
        assert isinstance(a.target, AgentCoreTarget)

    def test_target_discriminator_from_dict(self) -> None:
        """Pydantic should resolve the discriminator from a dict ``type`` field."""
        a = AgentConfig.model_validate(
            {
                "agent_id": "aid",
                "api_key": "key",
                "target": {"type": "http", "url": "https://x/y"},
            }
        )
        assert isinstance(a.target, HTTPTarget)

    def test_rejects_empty_agent_id(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            AgentConfig(agent_id="", api_key="k", target=HTTPTarget(url="https://x/y"))

    def test_rejects_empty_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            AgentConfig(
                agent_id="aid", api_key="", target=HTTPTarget(url="https://x/y")
            )

    @pytest.mark.parametrize(
        "agent",
        [
            {"message_only": "private-bearer"},
            {
                "target": {
                    "type": "http",
                    "url": "https://x/y",
                    "bearer_tokn": "private-bearer",
                }
            },
        ],
    )
    def test_rejects_unknown_option_without_echoing_it(
        self, agent: dict[str, object]
    ) -> None:
        config = {
            "agent_id": "aid",
            "api_key": "key",
            "target": {"type": "http", "url": "https://x/y"},
        } | agent
        with pytest.raises(ValueError, match="extra_forbidden") as error:
            AgentConfig.model_validate(config)
        assert "private-bearer" not in str(error.value)
