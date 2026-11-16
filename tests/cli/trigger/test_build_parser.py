"""band-trigger's options come from the CLI or the environment, and bad auth modes are rejected."""

from __future__ import annotations

import pytest

from band.cli.trigger import AuthMode, build_parser


class TestBuildParser:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("BAND_API_KEY", "env-key")
        monkeypatch.setenv("BAND_TARGET_HANDLE", "@env/agent")
        monkeypatch.setenv("BAND_MESSAGE", "env message")
        monkeypatch.setenv("BAND_AUTH_MODE", "user")

        parser = build_parser()
        args = parser.parse_args([])

        assert args.api_key == "env-key"
        assert args.target_handle == "@env/agent"
        assert args.message == "env message"
        assert args.auth_mode is AuthMode.USER

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("BAND_API_KEY", "env-key")

        parser = build_parser()
        args = parser.parse_args(["--api-key", "cli-key"])

        assert args.api_key == "cli-key"

    def test_default_rest_url(self, monkeypatch):
        # The default is os.environ.get("BAND_REST_URL", DEFAULT_REST_URL), so
        # clear the env to assert the built-in default rather than an ambient one.
        monkeypatch.delenv("BAND_REST_URL", raising=False)
        parser = build_parser()
        args = parser.parse_args([])

        assert args.rest_url == "https://app.band.ai/"

    def test_default_auth_mode(self, monkeypatch):
        monkeypatch.delenv("BAND_AUTH_MODE", raising=False)
        parser = build_parser()
        args = parser.parse_args([])

        assert args.auth_mode is AuthMode.AGENT

    def test_rejects_an_unknown_auth_mode_from_env(self, monkeypatch, capsys):
        monkeypatch.setenv("BAND_AUTH_MODE", "Agent")

        with pytest.raises(SystemExit):
            build_parser().parse_args([])

        assert "invalid AuthMode value: 'Agent'" in capsys.readouterr().err
