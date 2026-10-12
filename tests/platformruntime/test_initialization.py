"""PlatformRuntime initialization."""

from __future__ import annotations

from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import AgentConfig, SessionConfig


class TestInitialization:
    """Tests for PlatformRuntime initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert runtime.agent_id == "agent-123"
        assert runtime._api_key == "test-key"
        assert runtime._ws_url == "wss://app.band.ai/api/v1/socket/websocket"
        assert runtime._rest_url == "https://app.band.ai"
        assert isinstance(runtime._config, AgentConfig)
        assert isinstance(runtime._session_config, SessionConfig)

    def test_custom_urls(self):
        """Should accept custom URLs."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
            ws_url="wss://custom.example.com/ws",
            rest_url="https://custom.example.com",
        )

        assert runtime._ws_url == "wss://custom.example.com/ws"
        assert runtime._rest_url == "https://custom.example.com"

    def test_custom_configs(self):
        """Should accept custom configs."""
        config = AgentConfig(auto_subscribe_existing_rooms=False)
        session_config = SessionConfig(enable_context_cache=False)

        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
            config=config,
            session_config=session_config,
        )

        assert runtime._config is config
        assert runtime._session_config is session_config

    def test_initial_state(self):
        """Should have uninitialized state before start."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert runtime._link is None
        assert runtime._runtime is None
        assert runtime._agent_name == ""
        assert runtime._agent_description == ""
