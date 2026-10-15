"""The nightly live-log gate quiets HTTP client loggers and nothing else."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.baseline.conftest import (
    _HTTP_CLIENT_LOGGERS,
    pytest_configure,
    quiet_http_client_loggers,
)
from tests.paths import REPO_ROOT


def _log_config(level: str | None, ini: bool) -> SimpleNamespace:
    def getoption(name: str) -> str | None:
        assert name == "--log-cli-level"
        return level

    def getini(name: str) -> bool:
        assert name == "log_cli"
        return ini

    return SimpleNamespace(
        getoption=getoption,
        getini=getini,
        addinivalue_line=lambda *_args, **_kwargs: None,
        pluginmanager=SimpleNamespace(register=lambda *_args, **_kwargs: None),
    )


def _levels() -> dict[str, int]:
    return {name: logging.getLogger(name).level for name in _HTTP_CLIENT_LOGGERS}


class TestBaselineLiveLogging:
    def test_http_clients_stay_quiet_only_while_a_baseline_test_runs(self) -> None:
        previous = _levels()
        quiet = dict.fromkeys(_HTTP_CLIENT_LOGGERS, logging.WARNING)
        try:
            pytest_configure(_log_config("INFO", False))  # type: ignore[arg-type]
            assert _levels() == previous

            with quiet_http_client_loggers(_log_config(None, False)):  # type: ignore[arg-type]
                assert _levels() == previous
            assert _levels() == previous

            with quiet_http_client_loggers(_log_config("INFO", False)):  # type: ignore[arg-type]
                assert _levels() == quiet
            assert _levels() == previous

            with quiet_http_client_loggers(_log_config(None, True)):  # type: ignore[arg-type]
                assert _levels() == quiet
            assert _levels() == previous
        finally:
            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)

    @pytest.mark.timeout(60)
    def test_collecting_a_skipped_baseline_test_leaves_http_loggers_alone(
        self, tmp_path: Path
    ) -> None:
        names = ", ".join(repr(name) for name in _HTTP_CLIENT_LOGGERS)
        probe = tmp_path / "test_http_level.py"
        probe.write_text(
            "import logging\n"
            "\n"
            "def test_http_client_loggers_keep_their_level() -> None:\n"
            f"    names = ({names})\n"
            "    assert {name: logging.getLogger(name).level for name in names} "
            "== {name: logging.NOTSET for name in names}\n"
        )
        env = os.environ.copy()
        env["E2E_TESTS_ENABLED"] = "false"
        env["BAND_E2E_SCORECARD_JSON"] = ""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/baseline/guards/test_logs.py::test_scope_restores_the_level_when_the_scenario_fails",
                str(probe),
                "--log-cli-level=INFO",
                "--log-level=INFO",
                "-q",
                "--tb=short",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
