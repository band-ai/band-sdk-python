"""The nightly live-log gate quiets HTTP client loggers and nothing else."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from tests.e2e.baseline.conftest import _HTTP_CLIENT_LOGGERS, pytest_configure


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
    def test_http_clients_stay_quiet_only_while_live_logging_is_on(self) -> None:
        previous = _levels()
        try:
            pytest_configure(_log_config(None, False))  # type: ignore[arg-type]
            assert _levels() == previous

            pytest_configure(_log_config("INFO", False))  # type: ignore[arg-type]
            assert _levels() == dict.fromkeys(_HTTP_CLIENT_LOGGERS, logging.WARNING)

            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)
            pytest_configure(_log_config(None, True))  # type: ignore[arg-type]
            assert _levels() == dict.fromkeys(_HTTP_CLIENT_LOGGERS, logging.WARNING)
        finally:
            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)
