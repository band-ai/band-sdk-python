from __future__ import annotations

import argparse
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from band_rest.core.api_error import ApiError

from band.cli.trigger import (
    AuthMode,
    _format_api_error,
    build_parser,
    main,
    run,
)
from tests.logsupport import band_log_env, restored_logging

# --- Helpers ---


def _make_args(**overrides) -> argparse.Namespace:
    """Create a Namespace with sensible defaults for run()."""
    defaults = {
        "api_key": "test-api-key",
        "rest_url": "https://app.band.ai/",
        "auth_mode": "agent",
        "target_handle": "@owner/agent",
        "message": "Hello agent",
        "timeout": 120,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_asyncio_run(return_value=None, side_effect=None):
    """Create a fake asyncio.run that closes the coroutine to avoid warnings.

    This means main() tests don't exercise the real async path — that's intentional;
    the async flow is covered over the real REST client in tests/cli/triggerwire/.
    """

    def _run(coro):
        coro.close()
        if side_effect is not None:
            raise side_effect
        return return_value

    return _run


# --- build_parser tests ---


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
        assert args.auth_mode == "user"

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


# --- run() tests ---


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        args = _make_args(api_key=None)
        with pytest.raises(ValueError, match="API key is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_target_handle_raises(self):
        args = _make_args(target_handle=None)
        with pytest.raises(ValueError, match="Target handle is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_message_raises(self):
        args = _make_args(message=None)
        with pytest.raises(ValueError, match="Message is required"):
            await run(args)


# --- _format_api_error tests ---


class TestFormatApiError:
    def test_extracts_message_from_body(self):
        body = SimpleNamespace(
            error=SimpleNamespace(message="Chat room limit reached.")
        )
        err = ApiError(status_code=403, body=body)
        result = _format_api_error(err, "create chatroom")
        assert result == "Failed to create chatroom: Chat room limit reached."

    def test_falls_back_to_status_code(self):
        err = ApiError(status_code=500, body=None)
        result = _format_api_error(err, "send message")
        assert result == "Failed to send message: HTTP 500"

    def test_falls_back_when_no_error_attr(self):
        body = SimpleNamespace()
        err = ApiError(status_code=422, body=body)
        result = _format_api_error(err, "add participant")
        assert result == "Failed to add participant: HTTP 422"


# --- main() tests ---


class TestMain:
    def test_exits_0_on_success(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="room-ok"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0

    def test_exits_1_on_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("BAND_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["band-trigger"])

        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(
                    side_effect=ValueError("API key is required")
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_exits_1_on_unexpected_error(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=Exception("boom")),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_exits_1_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
                "--timeout",
                "30",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=TimeoutError()),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_timeout_error_message(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
                "--timeout",
                "45",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=TimeoutError()),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert "timed out after 45 seconds" in capsys.readouterr().err

    def test_writes_room_id_to_stdout(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="room-xyz"),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert capsys.readouterr().out.strip() == "room-xyz"

    def test_keeps_logs_off_the_machine_readable_stdout(self, monkeypatch, capsys):
        """BAND_LOG_STREAM must not be able to redirect logs onto stdout.

        stdout carries only the room ID for the caller to parse, so a log line
        written there makes a successful trigger unreadable — the stream is
        pinned rather than configurable.
        """
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )

        def _run_and_log(coro):
            coro.close()
            logging.getLogger("band.cli.trigger").info("probe line")
            return "room-xyz"

        with (
            restored_logging(),
            band_log_env(monkeypatch, STREAM="stdout", FILE=None),
            patch("band.cli.trigger.asyncio.run", side_effect=_run_and_log),
            pytest.raises(SystemExit),
        ):
            main()

        captured = capsys.readouterr()

        assert captured.out.strip() == "room-xyz"
        assert "probe line" in captured.err

    def test_writes_error_to_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "band.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=ValueError("bad input")),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert "bad input" in capsys.readouterr().err
