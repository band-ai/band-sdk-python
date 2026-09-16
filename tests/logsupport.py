"""Shared fixtures and context managers for logging tests.

Keeps process logging restoration and BAND_LOG_* env setup out of individual
tests so assertions stay intent-oriented.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

import band.logging_config as logging_config_module


class RecordingHandler(logging.Handler):
    """Stand-in for a host-owned handler (an OTEL ``LoggingHandler``, a shipper).

    Records what actually reached it, so composition tests assert on delivery
    rather than on handler bookkeeping.
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def restored_logging(*extra_loggers: str) -> Iterator[None]:
    """Snapshot and restore root, ``band``, and any extra named loggers."""
    loggers = [logging.getLogger(), logging.getLogger("band")]
    loggers.extend(logging.getLogger(name) for name in extra_loggers)
    snapshots = [_snapshot_logger(logger) for logger in loggers]
    try:
        yield
    finally:
        for logger, snapshot in zip(loggers, snapshots, strict=True):
            _restore_logger(logger, snapshot)


def _snapshot_logger(logger: logging.Logger) -> dict[str, Any]:
    return {
        "handlers": list(logger.handlers),
        "level": logger.level,
        "propagate": logger.propagate,
        "disabled": logger.disabled,
    }


def _restore_logger(logger: logging.Logger, snapshot: dict[str, Any]) -> None:
    for handler in list(logger.handlers):
        if handler not in snapshot["handlers"]:
            handler.close()
    logger.handlers[:] = snapshot["handlers"]
    logger.setLevel(snapshot["level"])
    logger.propagate = snapshot["propagate"]
    logger.disabled = snapshot["disabled"]


@contextmanager
def band_log_env(
    monkeypatch: pytest.MonkeyPatch,
    **fields: str | None,
) -> Iterator[None]:
    """Set ``BAND_LOG_*`` from short field names for the duration of the block.

    Pass ``FILE=None`` (or any key mapped to ``None``) to clear that variable.
    Keys are uppercased and prefixed with ``BAND_LOG_`` — e.g. ``LEVEL="debug"``
    sets ``BAND_LOG_LEVEL``.
    """
    with monkeypatch.context() as scoped:
        for env_name in tuple(os.environ):
            if env_name.startswith("BAND_LOG_"):
                scoped.delenv(env_name)

        for key, value in fields.items():
            env_name = f"BAND_LOG_{key.upper()}"
            if value is None:
                scoped.delenv(env_name, raising=False)
            else:
                scoped.setenv(env_name, value)
        yield


def fake_traceparent(monkeypatch: pytest.MonkeyPatch, *values: str) -> None:
    """Fake ``current_traceparent()`` for the rest of the test.

    One value fakes a fixed traceparent; more than one cycles through them on
    successive calls -- e.g. nested ``trace_context_scope()`` opens, each of
    which reads a fresh value at entry.
    """
    calls = iter(values)
    monkeypatch.setattr(
        logging_config_module, "current_traceparent", lambda: next(calls)
    )
