"""PlatformRuntime properties."""

from __future__ import annotations

import pytest

from band.runtime.platform_runtime import PlatformRuntime


class TestProperties:
    """Tests for PlatformRuntime properties."""

    def test_agent_id_property(self):
        """Should expose agent_id."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_id == "agent-123"

    def test_agent_name_empty_before_start(self):
        """agent_name should be empty before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_name == ""

    def test_agent_description_empty_before_start(self):
        """agent_description should be empty before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )
        assert runtime.agent_description == ""

    def test_link_raises_before_start(self):
        """link property should raise if not started."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="Runtime not started"):
            _ = runtime.link

    def test_runtime_raises_before_start(self):
        """runtime property should raise if not started."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        with pytest.raises(RuntimeError, match="Runtime not started"):
            _ = runtime.runtime
