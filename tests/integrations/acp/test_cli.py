"""Tests for ACP CLI entry point."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.integrations.acp.cli import main, parse_args


class TestParseArgs:
    """Tests for parse_args()."""

    def test_parse_args_required(self) -> None:
        """Should parse required arguments."""
        args = parse_args(
            [
                "--agent-id",
                "agent-123",
                "--api-key",
                "key-abc",
            ]
        )

        assert args.agent_id == "agent-123"
        assert args.api_key == "key-abc"

    def test_parse_args_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should use default values for optional args."""
        monkeypatch.delenv("THENVOI_REST_URL", raising=False)
        monkeypatch.delenv("THENVOI_WS_URL", raising=False)

        args = parse_args(
            [
                "--agent-id",
                "agent-123",
                "--api-key",
                "key-abc",
            ]
        )

        assert args.rest_url == "https://app.thenvoi.com"
        assert args.ws_url == "wss://app.thenvoi.com/api/v1/socket/websocket"
        assert args.log_level == "INFO"

    def test_parse_args_custom_urls(self) -> None:
        """Should accept custom REST and WS URLs."""
        args = parse_args(
            [
                "--agent-id",
                "agent-123",
                "--api-key",
                "key-abc",
                "--rest-url",
                "https://custom.example.com",
                "--ws-url",
                "wss://custom.example.com/ws",
            ]
        )

        assert args.rest_url == "https://custom.example.com"
        assert args.ws_url == "wss://custom.example.com/ws"

    def test_parse_args_log_level(self) -> None:
        """Should accept custom log level."""
        args = parse_args(
            [
                "--agent-id",
                "agent-123",
                "--api-key",
                "key-abc",
                "--log-level",
                "DEBUG",
            ]
        )

        assert args.log_level == "DEBUG"

    def test_parse_args_env_fallback(self) -> None:
        """Should fall back to environment variables."""
        with patch.dict(
            os.environ,
            {
                "THENVOI_AGENT_ID": "env-agent-id",
                "THENVOI_API_KEY": "env-api-key",
            },
        ):
            args = parse_args([])

        assert args.agent_id == "env-agent-id"
        assert args.api_key == "env-api-key"

    def test_parse_args_cli_overrides_env(self) -> None:
        """CLI args should take precedence over env vars."""
        with patch.dict(
            os.environ,
            {
                "THENVOI_AGENT_ID": "env-agent-id",
            },
        ):
            args = parse_args(["--agent-id", "cli-agent-id", "--api-key", "k"])

        assert args.agent_id == "cli-agent-id"


class TestMain:
    """Tests for main()."""

    @pytest.mark.asyncio
    async def test_main_missing_agent_id_raises(self) -> None:
        """Should raise ValueError when agent_id is missing."""
        args = parse_args(["--api-key", "key-abc"])
        args.agent_id = None

        with pytest.raises(ValueError, match="Agent ID is required"):
            await main(args)

    @pytest.mark.asyncio
    async def test_main_missing_api_key_raises(self) -> None:
        """Should raise ValueError when api_key is missing."""
        args = parse_args(["--agent-id", "agent-123"])
        args.api_key = None

        with pytest.raises(ValueError, match="API key is required"):
            await main(args)

    @pytest.mark.asyncio
    async def test_main_closes_adapter_after_run(self) -> None:
        """Should close the adapter REST client when the ACP server exits."""
        args = parse_args(["--agent-id", "agent-123", "--api-key", "key-abc"])
        mock_adapter = MagicMock()
        mock_adapter.close = AsyncMock()
        mock_agent = AsyncMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)

        with patch("thenvoi.Agent.create", return_value=mock_agent):
            with patch(
                "thenvoi.integrations.acp.server_adapter.ThenvoiACPServerAdapter",
                return_value=mock_adapter,
            ):
                with patch("thenvoi.integrations.acp.push_handler.ACPPushHandler"):
                    with patch("thenvoi.integrations.acp.server.ACPServer"):
                        with patch("acp.run_agent", new=AsyncMock(return_value=None)):
                            await main(args)

        mock_adapter.close.assert_awaited_once()
