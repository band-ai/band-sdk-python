"""Tests for environment-driven LogSettings."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from band import (
    LogSettings,
    LoggingStyle,
    LogStream,
    configure_logging_from_env,
)
from band.agent import Agent
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


def test_host_handler_on_band_logger_survives_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike root, a handler already attached to the named `band` logger must survive.

    dictConfig's non-incremental contract would otherwise clear it silently —
    breaking the natural "attach my shipper to band, leave root alone" pattern.
    """
    shipper = RecordingHandler()

    with restored_logging(), band_log_env(monkeypatch, LEVEL="INFO", FILE=None):
        logging.getLogger("band").addHandler(shipper)
        LogSettings().configure()
        logging.getLogger("band.runtime").info("shipped record")
        assert shipper in logging.getLogger("band").handlers

    assert shipper.messages == ["shipped record"]


def test_configure_leaves_the_whole_band_subtree_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming `band` in dictConfig's `loggers` reset every `band.*` child too.

    ``_handle_existing_loggers`` clears the handlers of a configured logger's
    children and returns them to NOTSET/propagate=True, so a host that attached
    a shipper to ``band.runtime`` or pinned ``band.platform`` to ERROR lost all
    of it. Dropping the section fixed the subtree, not just ``band`` itself —
    a different code path from the parent case above, and unguarded until now.
    """
    shipper = RecordingHandler()

    with restored_logging("band.runtime"), band_log_env(monkeypatch, FILE=None):
        child = logging.getLogger("band.runtime")
        child.addHandler(shipper)
        child.setLevel(logging.ERROR)
        child.propagate = False

        LogSettings().configure()

        assert child.handlers == [shipper]
        assert child.level == logging.ERROR
        assert child.propagate is False


def test_configure_does_not_reverse_a_hosts_propagate_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a logger's level must not silently re-enable propagation the host disabled.

    Every ``extra_loggers``/``band`` entry used to force ``propagate=True``
    regardless of what the host had deliberately set.
    """
    with (
        restored_logging("httpx"),
        band_log_env(monkeypatch, LEVEL="INFO", FILE=None),
    ):
        logging.getLogger("httpx").propagate = False
        logging.getLogger("band").propagate = False

        LogSettings().configure(extra_loggers={"httpx": "WARNING"})

        assert logging.getLogger("httpx").propagate is False
        assert logging.getLogger("band").propagate is False


def test_creating_an_agent_does_not_touch_root_logging() -> None:
    """No library code path may configure logging on its own.

    Only band-acp, band-trigger, the desktop server, and the docker launcher
    call configure_logging today. This drives ``Agent.create`` — the documented
    entry point, and where a convenience call would actually get added — rather
    than the bare constructor, which a regression would route around.
    """
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    Agent.create(
        adapter=MagicMock(),
        agent_id="agent-1",
        api_key="key-1",
    )

    assert root.handlers == handlers_before


def test_importing_the_sdk_does_not_touch_root_logging() -> None:
    """Importing a library must never configure the host's logging.

    ``band/__init__`` eagerly imports ``band.config.logs``, so this is one
    stray module-level ``configure()`` away from breaking, and it cannot be
    observed in-process: by the time a test runs, band is already imported.
    """
    probe = (
        "import band, logging, sys; "
        "sys.stdout.write(repr(logging.getLogger().handlers))"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "[]"


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
