"""Tests for ACP CLI entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.integrations.acp.cli import main, parse_args
from tests.logsupport import band_log_env, restored_logging


@contextmanager
def stubbed_acp_server() -> Iterator[MagicMock]:
    """Run ``main()`` without a platform connection; yields the stub adapter."""
    adapter = MagicMock()
    adapter.close = AsyncMock()
    agent = AsyncMock()
    agent.__aenter__ = AsyncMock(return_value=agent)
    agent.__aexit__ = AsyncMock(return_value=None)

    with ExitStack() as stack:
        stack.enter_context(patch("band.Agent.create", return_value=agent))
        stack.enter_context(
            patch(
                "band.integrations.acp.server_adapter.BandACPServerAdapter",
                return_value=adapter,
            )
        )
        stack.enter_context(patch("band.integrations.acp.push_handler.ACPPushHandler"))
        stack.enter_context(patch("band.integrations.acp.server.ACPServer"))
        stack.enter_context(
            patch(
                "band.integrations.acp.server.run_acp_server",
                new=AsyncMock(return_value=None),
            )
        )
        yield adapter


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
        monkeypatch.delenv("BAND_REST_URL", raising=False)
        monkeypatch.delenv("BAND_WS_URL", raising=False)

        args = parse_args(
            [
                "--agent-id",
                "agent-123",
                "--api-key",
                "key-abc",
            ]
        )

        assert args.rest_url == "https://app.band.ai"
        assert args.ws_url == "wss://app.band.ai/api/v1/socket/websocket"
        assert args.log_level is None  # omitted so BAND_LOG_LEVEL can apply

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
                "BAND_AGENT_ID": "env-agent-id",
                "BAND_API_KEY": "env-api-key",
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
                "BAND_AGENT_ID": "env-agent-id",
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

        with stubbed_acp_server() as adapter:
            await main(args)

        adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_main_keeps_logs_off_the_stdio_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """BAND_LOG_STREAM must not be able to redirect logs onto stdout.

        stdout carries the JSON-RPC frames, so a log line written there corrupts
        the editor's ACP session — the stream is pinned rather than configurable.
        """
        args = parse_args(["--agent-id", "agent-123", "--api-key", "key-abc"])

        with (
            restored_logging(),
            band_log_env(monkeypatch, STREAM="stdout", FILE=None),
            stubbed_acp_server(),
        ):
            await main(args)
            logging.getLogger("band.integrations.acp.cli").info("probe line")
            captured = capsys.readouterr()

        assert "probe line" in captured.err
        assert "probe line" not in captured.out
