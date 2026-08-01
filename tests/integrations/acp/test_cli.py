"""Tests for ACP CLI entry point (Fire + AcpCliConfig)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.integrations.acp.cli import entry_point, load_config, main, run
from band.integrations.acp.settings import AcpCliConfig, AcpEnvSettings, CliLogLevel
from tests.logsupport import band_log_env, restored_logging


@contextmanager
def stubbed_acp_server() -> Iterator[tuple[MagicMock, MagicMock]]:
    """Run ``main()`` without a platform connection; yields adapter and gateway."""
    adapter = MagicMock()
    adapter.close = AsyncMock()
    agent = MagicMock()
    gateway = MagicMock()
    gateway.__aenter__ = AsyncMock(return_value=gateway)
    gateway.__aexit__ = AsyncMock(return_value=None)
    gateway.serve = AsyncMock(return_value=None)

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
                "band.integrations.acp.host.ACPGateway",
                return_value=gateway,
            )
        )
        yield adapter, gateway


class TestFireSurface:
    """Fire accepts both hyphenated and underscored flags."""

    def test_hyphenated_flags(self) -> None:
        with patch("band.integrations.acp.cli.run") as mock_run:
            mock_run.return_value = None
            # entry_point wires Fire → run; patch run to observe kwargs Fire passes
            entry_point(["--agent-id", "agent-123", "--api-key", "key-abc"])
            mock_run.assert_called_once_with(
                agent_id="agent-123",
                api_key="key-abc",
            )

    def test_custom_urls_and_log_level(self) -> None:
        with patch("band.integrations.acp.cli.run") as mock_run:
            mock_run.return_value = None
            entry_point(
                [
                    "--agent-id",
                    "agent-123",
                    "--api-key",
                    "key-abc",
                    "--rest-url",
                    "https://custom.example.com",
                    "--ws-url",
                    "wss://custom.example.com/ws",
                    "--log-level",
                    "DEBUG",
                ]
            )
            mock_run.assert_called_once_with(
                agent_id="agent-123",
                api_key="key-abc",
                rest_url="https://custom.example.com",
                ws_url="wss://custom.example.com/ws",
                log_level="DEBUG",
            )


class TestLoadConfig:
    """Tests for validated :class:`AcpCliConfig` merge."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BAND_REST_URL", raising=False)
        monkeypatch.delenv("BAND_WS_URL", raising=False)
        monkeypatch.delenv("BAND_AGENT_ID", raising=False)
        monkeypatch.delenv("BAND_API_KEY", raising=False)

        config = load_config(agent_id="agent-123", api_key="key-abc")

        assert config.agent_id == "agent-123"
        assert config.api_key_value == "key-abc"
        assert config.rest_url == "https://app.band.ai"
        assert config.ws_url == "wss://app.band.ai/api/v1/socket/websocket"
        assert config.log_level is None

    def test_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BAND_AGENT_ID", "env-agent-id")
        monkeypatch.setenv("BAND_API_KEY", "env-api-key")
        monkeypatch.delenv("BAND_REST_URL", raising=False)
        monkeypatch.delenv("BAND_WS_URL", raising=False)

        config = load_config()

        assert config.agent_id == "env-agent-id"
        assert config.api_key_value == "env-api-key"

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BAND_AGENT_ID", "env-agent-id")
        monkeypatch.setenv("BAND_API_KEY", "env-api-key")

        config = load_config(agent_id="cli-agent-id", api_key="k")

        assert config.agent_id == "cli-agent-id"
        assert config.api_key_value == "k"

    def test_rejects_non_http_rest_url(self) -> None:
        with pytest.raises(ValueError, match="rest_url must be an http"):
            AcpCliConfig.from_cli(
                agent_id="a",
                api_key="k",
                rest_url="ftp://example.com",
                env=AcpEnvSettings(
                    agent_id=None,
                    api_key=None,
                    rest_url="https://app.band.ai",
                    ws_url="wss://app.band.ai/api/v1/socket/websocket",
                ),
            )

    def test_rejects_non_ws_url(self) -> None:
        with pytest.raises(ValueError, match="ws_url must be a ws"):
            AcpCliConfig.from_cli(
                agent_id="a",
                api_key="k",
                ws_url="https://example.com/ws",
                env=AcpEnvSettings(
                    agent_id=None,
                    api_key=None,
                    rest_url="https://app.band.ai",
                    ws_url="wss://app.band.ai/api/v1/socket/websocket",
                ),
            )

    def test_log_level_enum(self) -> None:
        config = AcpCliConfig.from_cli(
            agent_id="a",
            api_key="k",
            log_level="debug",
            env=AcpEnvSettings(
                agent_id=None,
                api_key=None,
                rest_url="https://app.band.ai",
                ws_url="wss://app.band.ai/api/v1/socket/websocket",
            ),
        )
        assert config.log_level is CliLogLevel.DEBUG


class TestMain:
    """Tests for main() / run()."""

    @pytest.mark.asyncio
    async def test_main_missing_agent_id_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAND_AGENT_ID", raising=False)

        with pytest.raises(ValueError, match="Agent ID is required"):
            await main(api_key="key-abc")

    @pytest.mark.asyncio
    async def test_main_missing_api_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAND_API_KEY", raising=False)

        with pytest.raises(ValueError, match="API key is required"):
            await main(agent_id="agent-123")

    @pytest.mark.asyncio
    async def test_main_uses_acp_gateway(self) -> None:
        with stubbed_acp_server() as (_adapter, gateway):
            await main(agent_id="agent-123", api_key="key-abc")

        gateway.__aenter__.assert_awaited_once()
        gateway.serve.assert_awaited_once()
        gateway.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_main_keeps_logs_off_the_stdio_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """BAND_LOG_STREAM must not redirect logs onto stdout (ACP JSON-RPC)."""
        with (
            restored_logging(),
            band_log_env(monkeypatch, STREAM="stdout", FILE=None),
            stubbed_acp_server(),
        ):
            await main(agent_id="agent-123", api_key="key-abc")
            logging.getLogger("band.integrations.acp.cli").info("probe line")
            captured = capsys.readouterr()

        assert "probe line" in captured.err
        assert "probe line" not in captured.out

    def test_run_exits_on_validation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAND_AGENT_ID", raising=False)
        monkeypatch.delenv("BAND_API_KEY", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            run(api_key="only-key")
        assert exc_info.value.code == 1
