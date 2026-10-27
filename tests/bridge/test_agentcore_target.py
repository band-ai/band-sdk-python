"""AgentCoreTarget behavior."""

from __future__ import annotations

import pytest
from bridge_core.config import (
    AgentCoreTarget,
)


class TestAgentCoreTarget:
    def test_basic(self) -> None:
        t = AgentCoreTarget(
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/abc"
        )
        assert t.region == "us-east-1"
        assert t.type == "agentcore"

    def test_rejects_empty_arn(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AgentCoreTarget(runtime_arn="")

    def test_rejects_empty_region(self) -> None:
        with pytest.raises(ValueError, match="region"):
            AgentCoreTarget(
                runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/a", region=""
            )

    def test_rejects_misspelled_option(self) -> None:
        with pytest.raises(ValueError, match="extra_forbidden"):
            AgentCoreTarget.model_validate(
                {"runtime_arn": "arn:x", "regoin": "us-east-1"}
            )
