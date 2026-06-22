from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Iterator

import pytest

from band import BandConfigError, build_logging_config, configure_logging


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    band_logger = logging.getLogger("band")
    noisy_logger = logging.getLogger("third_party.noisy")

    root_state = (list(root.handlers), root.level, root.disabled)
    band_state = (
        list(band_logger.handlers),
        band_logger.level,
        band_logger.propagate,
        band_logger.disabled,
    )
    noisy_state = (
        list(noisy_logger.handlers),
        noisy_logger.level,
        noisy_logger.propagate,
        noisy_logger.disabled,
    )

    try:
        yield
    finally:
        root.handlers[:] = root_state[0]
        root.setLevel(root_state[1])
        root.disabled = root_state[2]

        band_logger.handlers[:] = band_state[0]
        band_logger.setLevel(band_state[1])
        band_logger.propagate = band_state[2]
        band_logger.disabled = band_state[3]

        noisy_logger.handlers[:] = noisy_state[0]
        noisy_logger.setLevel(noisy_state[1])
        noisy_logger.propagate = noisy_state[2]
        noisy_logger.disabled = noisy_state[3]


def test_build_logging_config_returns_fresh_normalized_dict(monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "pythonjsonlogger":
            return object()
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    config = build_logging_config(
        style="json",
        stream="stdout",
        extra_loggers={"band_parlant_agent": "DEBUG"},
        static_fields={"service": "agent"},
    )
    second_config = build_logging_config()

    assert config is not second_config
    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False
    assert config["handlers"]["console"]["stream"] == "ext://sys.stdout"
    assert config["root"] == {"level": logging.WARNING, "handlers": ["console"]}
    assert config["loggers"]["band"] == {"level": logging.INFO, "propagate": True}
    assert config["loggers"]["band_parlant_agent"] == {
        "level": "DEBUG",
        "propagate": True,
    }

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
        if name == "pythonjsonlogger":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(BandConfigError, match=r"band-sdk\[logging\]"):
        build_logging_config(style="json")


def test_configure_logging_shows_band_logs_and_suppresses_noisy_info(
    capsys,
    restore_logging,
) -> None:
    configure_logging()

    logging.getLogger("band.runtime").info("band visible")
    logging.getLogger("third_party.noisy").info("dependency hidden")

    captured = capsys.readouterr()
    assert "band visible" in captured.err
    assert "dependency hidden" not in captured.err


def test_configure_logging_json_outputs_machine_readable_records(
    capsys,
    restore_logging,
) -> None:
    pytest.importorskip("pythonjsonlogger")

    configure_logging(style="json", stream="stdout", static_fields={"service": "agent"})
    logging.getLogger("band.runtime").info("json visible")

    captured = capsys.readouterr()
    record = json.loads(captured.out)
    assert record["level"] == "INFO"
    assert record["logger"] == "band.runtime"
    assert record["message"] == "json visible"
    assert record["service"] == "agent"
