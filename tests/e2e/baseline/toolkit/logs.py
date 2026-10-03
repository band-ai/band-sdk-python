"""Scoped SDK log levels for baseline scenarios."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def sdk_logs_at(logger_name: str, level: int) -> Iterator[None]:
    """Let ``logger_name`` records at ``level`` reach pytest's captured log, which
    a failing test prints; the root logger's WARNING default otherwise drops them.
    Never raises the threshold, so a run at ``--log-level=DEBUG`` keeps its DEBUG."""
    sdk_logger = logging.getLogger(logger_name)
    previous_level = sdk_logger.level
    sdk_logger.setLevel(min(level, sdk_logger.getEffectiveLevel()))
    try:
        yield
    finally:
        sdk_logger.setLevel(previous_level)
