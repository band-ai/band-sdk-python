"""Agent compositor: CreateFactory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from band.agent import Agent
from band.runtime.types import AgentConfig, SessionConfig


class TestCreateFactory:
    """Tests for Agent.create() factory method."""

    def test_creates_with_default_urls(self, mock_adapter, monkeypatch):
        """Should create agent with default URLs."""
        # Hermetic: a developer's shell may export the env overrides.
        monkeypatch.delenv("BAND_WS_URL", raising=False)
        monkeypatch.delenv("BAND_REST_URL", raising=False)
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
            )

            mock_runtime_class.assert_called_once_with(
                agent_id="agent-123",
                api_key="test-key",
                ws_url="wss://app.band.ai/api/v1/socket/websocket",
                rest_url="https://app.band.ai",
                config=None,
                session_config=None,
                contact_config=None,
                on_participant_added=None,
                on_participant_removed=None,
            )

    def test_creates_with_custom_urls(self, mock_adapter):
        """Should accept custom URLs."""
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                ws_url="wss://custom.example.com/ws",
                rest_url="https://custom.example.com",
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["ws_url"] == "wss://custom.example.com/ws"
            assert call_kwargs["rest_url"] == "https://custom.example.com"

    def test_urls_resolve_from_environment(self, mock_adapter, monkeypatch):
        """Omitted URLs resolve from BAND_WS_URL / BAND_REST_URL env vars."""
        monkeypatch.setenv("BAND_WS_URL", "wss://env.example.com/ws")
        monkeypatch.setenv("BAND_REST_URL", "https://env.example.com")
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime_class.return_value = MagicMock()

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["ws_url"] == "wss://env.example.com/ws"
            assert call_kwargs["rest_url"] == "https://env.example.com"

    def test_explicit_urls_beat_environment(self, mock_adapter, monkeypatch):
        """An explicit argument wins over the environment variable."""
        monkeypatch.setenv("BAND_REST_URL", "https://env.example.com")
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime_class.return_value = MagicMock()

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                rest_url="https://explicit.example.com",
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["rest_url"] == "https://explicit.example.com"

    def test_creates_with_configs(self, mock_adapter):
        """Should accept custom configs."""
        config = AgentConfig()
        session_config = SessionConfig()

        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                config=config,
                session_config=session_config,
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["config"] is config
            assert call_kwargs["session_config"] is session_config

    def test_creates_with_custom_preprocessor(self, mock_adapter, mock_preprocessor):
        """Should accept custom preprocessor."""
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            agent = Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                preprocessor=mock_preprocessor,
            )

            assert agent._preprocessor is mock_preprocessor

    def test_creates_with_participant_callbacks(self, mock_adapter):
        """Should pass participant callbacks through to PlatformRuntime."""
        with patch("band.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime
            on_participant_added = AsyncMock()
            on_participant_removed = AsyncMock()

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                on_participant_added=on_participant_added,
                on_participant_removed=on_participant_removed,
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["on_participant_added"] is on_participant_added
            assert call_kwargs["on_participant_removed"] is on_participant_removed
