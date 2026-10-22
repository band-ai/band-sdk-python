"""Preserve the CLI outcome and unexpected interruptions through cleanup."""

from __future__ import annotations

import json
import signal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from remote_agent.cli import main


class TestCliLifecycle:
    @pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(23)])
    def test_failed_cleanup_does_not_suppress_an_interruption(
        self, interruption: BaseException, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runtime = SimpleNamespace(
            http=SimpleNamespace(url="http://127.0.0.1:1"),
            run=Mock(side_effect=interruption),
            close=Mock(side_effect=OSError("private shutdown details")),
        )
        with (
            patch("remote_agent.cli.require_runtime"),
            patch("remote_agent.cli.load_config", return_value={"kind": "room"}),
            patch("remote_agent.cli.Service", return_value=runtime),
            patch("remote_agent.cli.signal.signal", return_value=signal.SIG_DFL),
            pytest.raises(type(interruption)) as raised,
        ):
            main(["serve", "unused.json"])
        assert raised.value is interruption
        runtime.close.assert_called_once_with()
        diagnostic = capsys.readouterr().err
        assert json.loads(diagnostic) == {
            "event": "shutdown_failed",
            "category": "OSError",
        }
        assert "private shutdown details" not in diagnostic

    @pytest.mark.parametrize(
        "close_error, expected", [(None, 0), (OSError("closed"), 1)]
    )
    def test_cleanup_failure_is_reported_as_an_unsuccessful_exit(
        self,
        close_error: OSError | None,
        expected: int,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runtime = SimpleNamespace(
            http=SimpleNamespace(url="http://127.0.0.1:1"),
            run=Mock(),
            close=Mock(side_effect=close_error),
        )
        with (
            patch("remote_agent.cli.require_runtime"),
            patch("remote_agent.cli.load_config", return_value={"kind": "room"}),
            patch("remote_agent.cli.Service", return_value=runtime),
            patch("remote_agent.cli.signal.signal", return_value=signal.SIG_DFL),
        ):
            assert main(["serve", "unused.json"]) == expected
        runtime.close.assert_called_once_with()
        diagnostic = capsys.readouterr().err
        if close_error is None:
            assert diagnostic == ""
        else:
            assert json.loads(diagnostic) == {
                "event": "shutdown_failed",
                "category": "OSError",
            }
