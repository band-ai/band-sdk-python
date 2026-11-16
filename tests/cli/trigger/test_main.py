"""band-trigger's entry point: exit codes, stderr errors and a stdout carrying only the room id."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from band.cli.trigger import main
from tests.logsupport import band_log_env, restored_logging


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
