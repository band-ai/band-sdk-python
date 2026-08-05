from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import re
from pathlib import Path

import pytest

from band import (
    BandConfigError,
    LoggingStyle,
    LogStream,
    build_logging_config,
    configure_logging,
)
from band.logging_config import JSON_LOGGER_REQUIREMENT, OTEL_CORRELATION_FIELDS
from tests.logsupport import restored_logging


def test_configure_logging_signature_matches_builder() -> None:
    """configure_logging forwards fifteen kwargs by hand, one call at a time.

    A parameter added to the builder alone would be silently unreachable
    through the function most callers use.
    """
    assert inspect.signature(configure_logging) == inspect.signature(
        build_logging_config
    )


def test_build_logging_config_returns_fresh_normalized_dict(monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "pythonjsonlogger.json":
            return object()
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    config = build_logging_config(
        style=LoggingStyle.JSON,
        stream=LogStream.STDOUT,
        extra_loggers={"band_parlant_agent": "DEBUG"},
        static_fields={"service": "agent"},
    )
    second_config = build_logging_config()

    assert config is not second_config
    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False
    assert config["handlers"]["console"]["stream"] == "ext://sys.stdout"
    assert config["root"] == {"level": logging.WARNING, "handlers": ["console"]}
    assert config["loggers"]["band"] == {"level": logging.INFO}
    assert config["loggers"]["band_parlant_agent"] == {"level": "DEBUG"}

    formatter = config["formatters"]["console"]
    assert formatter["()"] == "pythonjsonlogger.json.JsonFormatter"
    assert formatter["rename_fields"] == {
        "asctime": "timestamp",
        "levelname": "level",
        "name": "logger",
    }
    assert formatter["static_fields"] == {"service": "agent"}


def test_json_style_requires_optional_dependency(monkeypatch) -> None:
    def fake_find_spec(name: str):
        if name == "pythonjsonlogger.json":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(BandConfigError, match=r"band-sdk\[logging\]"):
        build_logging_config(style=LoggingStyle.JSON)


def test_json_style_raises_config_error_when_package_absent(monkeypatch) -> None:
    def fake_find_spec(name: str):
        if name.startswith("pythonjsonlogger"):
            raise ModuleNotFoundError(name="pythonjsonlogger")
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(BandConfigError, match=re.escape(JSON_LOGGER_REQUIREMENT)):
        build_logging_config(style=LoggingStyle.JSON)


def test_bool_levels_are_rejected() -> None:
    with pytest.raises(ValueError, match="level must be an int or logging level name"):
        build_logging_config(level=True)

    with pytest.raises(
        ValueError, match="root_level must be an int or logging level name"
    ):
        build_logging_config(root_level=False)


def test_configure_logging_shows_band_logs_and_suppresses_noisy_info(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with restored_logging("third_party.noisy"):
        configure_logging()
        logging.getLogger("band.runtime").info("band visible")
        logging.getLogger("third_party.noisy").info("dependency hidden")
        err = capsys.readouterr().err

    assert "band visible" in err
    assert "dependency hidden" not in err


def test_configure_logging_json_outputs_machine_readable_records(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with restored_logging():
        configure_logging(
            style=LoggingStyle.JSON,
            stream=LogStream.STDOUT,
            static_fields={"service": "agent"},
        )
        logging.getLogger("band.runtime").info("json visible")
        record = json.loads(capsys.readouterr().out)

    assert record["level"] == "INFO"
    assert record["logger"] == "band.runtime"
    assert record["message"] == "json visible"
    assert record["service"] == "agent"


def mode(path: Path) -> str:
    """The path's permission bits, spelled the way the octal literal reads."""
    return oct(path.stat().st_mode & 0o777)


def _json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def test_json_records_carry_null_trace_context_without_instrumentation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An uninstrumented process still emits the correlation keys, as null.

    The schema must not change shape when a host later turns tracing on.
    """
    with restored_logging():
        configure_logging(style=LoggingStyle.JSON, stream=LogStream.STDOUT)
        logging.getLogger("band.runtime").info("uncorrelated")
        record = _json_line(capsys)

    assert {field: record[field] for field in OTEL_CORRELATION_FIELDS} == (
        dict.fromkeys(OTEL_CORRELATION_FIELDS)
    )


def test_json_records_keep_injected_trace_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Values OpenTelemetry puts on the record reach the JSON line unchanged.

    The instrumentor sets these as record attributes, which is what ``extra``
    simulates here — see ``tests/example_agents/test_otel_setup.py`` for the
    same assertion driven by a real span.
    """
    injected: dict[str, object] = {
        "otelTraceID": "0af7651916cd43dd8448eb211c80319c",
        "otelSpanID": "b7ad6b7169203331",
        "otelTraceSampled": True,
        "otelServiceName": "band-agent",
    }

    with restored_logging():
        configure_logging(style=LoggingStyle.JSON, stream=LogStream.STDOUT)
        logging.getLogger("band.runtime").info("correlated", extra=injected)
        record = _json_line(capsys)

    assert {field: record[field] for field in OTEL_CORRELATION_FIELDS} == injected


def test_custom_json_fields_replace_the_default_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``json_fields`` is the whole schema, correlation keys included.

    Documented as a replacement rather than an addition: a caller who asks for
    two fields gets two, and splices ``OTEL_CORRELATION_FIELDS`` back in when
    they want correlation.
    """
    with restored_logging():
        configure_logging(
            style=LoggingStyle.JSON,
            stream=LogStream.STDOUT,
            json_fields=("levelname", "message"),
        )
        logging.getLogger("band.runtime").info("terse")
        record = _json_line(capsys)

    assert record == {"level": "INFO", "message": "terse"}


def test_configure_logging_rich_honors_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("rich")

    with restored_logging():
        configure_logging(style=LoggingStyle.RICH, stream=LogStream.STDOUT)
        logging.getLogger("band.runtime").info("rich visible")
        captured = capsys.readouterr()

    assert "rich visible" in captured.out
    assert "rich visible" not in captured.err


def test_rich_style_passes_datefmt_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "rich":
            return object()
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    config = build_logging_config(style=LoggingStyle.RICH, datefmt="%H:%M:%S")

    assert config["handlers"]["console"]["datefmt"] == "%H:%M:%S"


def test_build_logging_config_does_not_touch_filesystem(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "agent.log"

    build_logging_config(log_file=log_file)

    assert not log_file.parent.exists()


def test_size_cap_without_a_backup_is_rejected(tmp_path: Path) -> None:
    """A RotatingFileHandler with backupCount=0 reopens the file and grows forever.

    Silently accepting it hands back unbounded growth to the caller who asked
    for a size cap, so it is refused rather than half-honored.
    """
    with pytest.raises(ValueError, match="backup_count must be at least 1"):
        build_logging_config(
            log_file=tmp_path / "a.log", max_bytes=1_000, backup_count=0
        )


def test_rotation_rolls_over_and_keeps_the_requested_backups(tmp_path: Path) -> None:
    """A size cap rotates, and keeps exactly the backups asked for — no more."""
    log_file = tmp_path / "agent.log"

    with restored_logging():
        configure_logging(log_file=log_file, max_bytes=200, backup_count=2)
        for _ in range(40):
            logging.getLogger("band.runtime").info("x" * 60)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "agent.log",
        "agent.log.1",
        "agent.log.2",
    ]
    assert log_file.stat().st_size <= 200
    # A rollover replaces the live file, so a mode set once at configure time
    # protects only the first generation — and a long-lived agent's newest log
    # is the one holding the most room content.
    assert [mode(path) for path in sorted(tmp_path.iterdir())] == ["0o600"] * 3


def test_per_logger_level_reaches_the_file_too(tmp_path: Path) -> None:
    """An extra_loggers override must land in the durable sink, not just the console.

    Both handlers stay unpinned when console and file share a level, so the
    logger's own level is the only gate.
    """
    log_file = tmp_path / "agent.log"

    with restored_logging("httpx"):
        configure_logging(
            level="INFO", log_file=log_file, extra_loggers={"httpx": "DEBUG"}
        )
        logging.getLogger("httpx").debug("httpx detail")

    assert "httpx detail" in log_file.read_text()


def test_file_level_can_capture_debug_without_console_noise(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_file = tmp_path / "agent.log"

    with restored_logging():
        configure_logging(level="INFO", log_file=log_file, file_level="DEBUG")
        logging.getLogger("band.runtime").debug("debug detail")
        err = capsys.readouterr().err

    assert "debug detail" not in err
    assert "debug detail" in log_file.read_text()


def test_negative_rotation_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        build_logging_config(log_file=tmp_path / "a.log", max_bytes=-1)

    with pytest.raises(ValueError, match="backup_count"):
        build_logging_config(log_file=tmp_path / "a.log", backup_count=-1)


def test_invalid_file_style_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="file_style"):
        build_logging_config(
            log_file=tmp_path / "a.log",
            file_style=LoggingStyle.RICH,  # type: ignore[arg-type]
        )


def test_fmt_is_ignored_by_non_standard_console_style() -> None:
    """fmt only feeds the standard formatter; rich/json never accept it.

    Rejecting fmt+style="json" used to fire on a combination that was already
    inert, since the rich/json console builders don't take a fmt argument at
    all — there was nothing to reject.
    """
    config = build_logging_config(style=LoggingStyle.JSON, fmt="%(message)s")

    assert (
        config["formatters"]["console"]["()"] == "pythonjsonlogger.json.JsonFormatter"
    )


def test_fmt_configures_cleanly_with_a_json_file_sink(tmp_path: Path) -> None:
    """The file-style arm of the same trap: an author's fmt vs. an env-driven file_style.

    fmt is chosen by the code author; file_style is usually environment-driven
    (BAND_LOG_FILE_STYLE). The console keeps the custom format while the file
    sink is JSON, independently.
    """
    config = build_logging_config(
        fmt="%(message)s",
        log_file=tmp_path / "agent.log",
        file_style=LoggingStyle.JSON,
    )

    assert config["formatters"]["console"]["format"] == "%(message)s"
    assert config["formatters"]["file"]["()"] == "pythonjsonlogger.json.JsonFormatter"


def test_configure_logging_creates_parent_directory(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "agent.log"

    with restored_logging():
        configure_logging(log_file=log_file)
        logging.getLogger("band.runtime").info("written to file")

    assert log_file.exists()
    assert "written to file" in log_file.read_text()


def test_band_creates_its_log_directories_and_file_owner_only(tmp_path: Path) -> None:
    """Band logs message content at DEBUG, so a log file can hold room content.

    Every segment Band creates is hardened, not just the leaf — which is all
    ``mkdir(mode=..., parents=True)`` would do, leaving the intermediates at the
    umask default.
    """
    log_file = tmp_path / "var" / "band" / "agent.log"

    with restored_logging():
        configure_logging(log_file=log_file)

    created = (log_file.parent.parent, log_file.parent)
    assert [mode(path) for path in created] == ["0o700", "0o700"]
    assert mode(log_file) == "0o600"


def test_paths_band_did_not_create_keep_the_modes_they_have(tmp_path: Path) -> None:
    """Hardening reaches only what Band creates; the rest is the operator's.

    Narrowing what is already there reaches outside Band entirely: as root, a
    log path under /tmp would take the whole box's 1777 down to 0700, and a log
    file an operator deliberately made group-readable is theirs to set.
    """
    directory = tmp_path / "operator-owned"
    directory.mkdir()
    directory.chmod(0o755)
    log_file = directory / "agent.log"
    log_file.touch()
    log_file.chmod(0o644)

    with restored_logging():
        configure_logging(log_file=log_file)

    assert (mode(directory), mode(log_file)) == ("0o755", "0o644")


def test_configure_logging_twice_does_not_duplicate_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with restored_logging():
        configure_logging()
        configure_logging()
        logging.getLogger("band.runtime").info("once")
        err = capsys.readouterr().err

    assert err.count("once") == 1
