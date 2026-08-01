"""Tests for environment-driven LogSettings."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from band import (
    LogSettings,
    LoggingStyle,
    LogStream,
    configure_logging_from_env,
)
from tests.logsupport import (
    RecordingHandler,
    band_log_env,
    restored_logging,
)


def test_band_log_env_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "agent.log"
    with band_log_env(
        monkeypatch,
        LEVEL="debug",
        ROOT_LEVEL="error",
        FILE=str(log_file),
        FILE_LEVEL="warning",
        MAX_BYTES="1000000",
        BACKUPS="2",
        CONSOLE_STYLE="json",
        FILE_STYLE="json",
        STREAM="stdout",
        OVERRIDES='{"httpx": "warning"}',
    ):
        settings = LogSettings()

    assert settings.log_level == "DEBUG"
    assert settings.log_root_level == "ERROR"
    assert settings.log_file == log_file
    assert settings.log_file_level == "WARNING"
    assert settings.log_max_bytes == 1_000_000
    assert settings.log_backups == 2
    assert settings.log_console_style == LoggingStyle.JSON
    assert settings.log_file_style == LoggingStyle.JSON
    assert settings.log_stream == LogStream.STDOUT
    assert settings.log_overrides == {"httpx": "WARNING"}


def test_empty_env_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    with band_log_env(monkeypatch, LEVEL="", MAX_BYTES=""):
        settings = LogSettings()

    assert settings.log_level == "INFO"
    assert settings.log_max_bytes == 0


def test_invalid_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with band_log_env(monkeypatch, LEVEL="LOUD"):
        with pytest.raises(ValidationError, match="must be a valid logging level"):
            LogSettings()


def test_explicit_init_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with band_log_env(monkeypatch, LEVEL="DEBUG"):
        settings = LogSettings(log_level="WARNING")

    assert settings.log_level == "WARNING"


def test_create_omits_none_so_env_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with band_log_env(monkeypatch, LEVEL="DEBUG"):
        from_env = LogSettings.create(log_level=None)
        overridden = LogSettings.create(log_level="WARNING")

    assert from_env.log_level == "DEBUG"
    assert overridden.log_level == "WARNING"


def test_env_overrides_win_over_consumer_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with band_log_env(monkeypatch, OVERRIDES='{"httpx": "DEBUG"}'):
        config = LogSettings().build_config(
            extra_loggers={"httpx": "WARNING", "httpcore": "WARNING"}
        )

    assert config["loggers"]["httpx"]["level"] == "DEBUG"
    assert config["loggers"]["httpcore"]["level"] == "WARNING"


def test_configure_logging_from_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with restored_logging(), band_log_env(monkeypatch, LEVEL="DEBUG", FILE=None):
        configure_logging_from_env()
        logging.getLogger("band.runtime").debug("from env helper")
        err = capsys.readouterr().err

    assert "from env helper" in err


def test_subclass_defaults_keep_the_base_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consumers subclass to move a default; that must not cost them validation.

    Redeclaring a field replaces its annotation, so any coercion attached there
    would be silently dropped — and ``BAND_LOG_STREAM=STDOUT`` would start
    raising for the subclass while working for the base.
    """

    class StdoutLogSettings(LogSettings):
        log_stream: LogStream = LogStream.STDOUT

    with band_log_env(monkeypatch, STREAM="STDERR"):
        assert StdoutLogSettings().log_stream == LogStream.STDERR

    with band_log_env(monkeypatch, STREAM=None):
        assert StdoutLogSettings().log_stream == LogStream.STDOUT


def test_host_telemetry_handler_must_be_attached_after_band_configures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Band applies a non-incremental ``dictConfig``, which replaces root's handlers.

    So a host-owned telemetry handler (OpenTelemetry's ``LoggingHandler``, a log
    shipper) only sees ``band.*`` records when it is attached *after*
    ``LogSettings.configure()`` — the order ``examples/opentelemetry`` documents.
    Attaching first is silently useless, which is why both orders are pinned here.
    """
    attached_before = RecordingHandler()
    attached_after = RecordingHandler()

    with restored_logging(), band_log_env(monkeypatch, LEVEL="INFO", FILE=None):
        logging.getLogger().addHandler(attached_before)
        LogSettings().configure()
        logging.getLogger().addHandler(attached_after)
        logging.getLogger("band.runtime").info("telemetry record")

    assert attached_after.messages == ["telemetry record"]
    assert attached_before.messages == []


def test_application_configuration_honors_explicit_root_level(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        restored_logging("application.runner"),
        band_log_env(monkeypatch, LEVEL="INFO", ROOT_LEVEL=None, FILE=None),
    ):
        LogSettings().for_application().configure()
        logging.getLogger("application.runner").info("application visible")
        err = capsys.readouterr().err

    with (
        restored_logging("application.runner"),
        band_log_env(monkeypatch, LEVEL="INFO", ROOT_LEVEL="ERROR", FILE=None),
    ):
        LogSettings().for_application().configure()
        logging.getLogger("application.runner").info("application hidden")
        explicit_err = capsys.readouterr().err

    assert "application visible" in err
    assert "application hidden" not in explicit_err
